#include "Runtime/KLightsSubsystem.h"

#include "Camera/PlayerCameraManager.h"
#include "Core/Optics.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "KLightsLog.h"
#include "Materials/MaterialInterface.h"
#include "Misc/CommandLine.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Runtime/KLightsFixture.h"
#include "Runtime/KLightsGameMode.h"
#include "Runtime/KLightsStage.h"
#include "UnrealClient.h"

namespace
{
	const TCHAR* DefaultEngineUrl = TEXT("http://127.0.0.1:8765");

	/** A setting from the command line (-Key=Value), else [KLights] Key in Game.ini. */
	FString Setting(const TCHAR* Key, const FString& Default)
	{
		FString Value;
		if (FParse::Value(FCommandLine::Get(), *FString::Printf(TEXT("%s="), Key), Value) && !Value.IsEmpty())
		{
			return Value;
		}
		if (GConfig != nullptr && GConfig->GetString(TEXT("KLights"), Key, Value, GGameIni) && !Value.IsEmpty())
		{
			return Value;
		}
		return Default;
	}

	template <typename T>
	T* Load(const TCHAR* Path, FString& Missing)
	{
		T* Found = LoadObject<T>(nullptr, Path);
		if (Found == nullptr)
		{
			Missing += Missing.IsEmpty() ? Path : FString(TEXT(", ")) + Path;
		}
		return Found;
	}
}

bool UKLightsSubsystem::DoesSupportWorldType(const EWorldType::Type WorldType) const
{
	return WorldType == EWorldType::Game || WorldType == EWorldType::PIE;
}

TStatId UKLightsSubsystem::GetStatId() const
{
	RETURN_QUICK_DECLARE_CYCLE_STAT(UKLightsSubsystem, STATGROUP_Tickables);
}

bool UKLightsSubsystem::LoadAssets()
{
	FString Missing;
	Cube = Load<UStaticMesh>(TEXT("/Engine/BasicShapes/Cube.Cube"), Missing);
	Sphere = Load<UStaticMesh>(TEXT("/Engine/BasicShapes/Sphere.Sphere"), Missing);
	Cylinder = Load<UStaticMesh>(TEXT("/Engine/BasicShapes/Cylinder.Cylinder"), Missing);
	Cone = Load<UStaticMesh>(TEXT("/Engine/BasicShapes/Cone.Cone"), Missing);
	Plane = Load<UStaticMesh>(TEXT("/Engine/BasicShapes/Plane.Plane"), Missing);
	SurfaceMaterial = Load<UMaterialInterface>(TEXT("/Game/Previz/Materials/M_PrevizModel.M_PrevizModel"), Missing);
	BeamMaterial = Load<UMaterialInterface>(TEXT("/Game/Previz/Materials/M_PrevizBeam.M_PrevizBeam"), Missing);
	DotMaterial = Load<UMaterialInterface>(TEXT("/Game/Previz/Materials/M_PrevizDot.M_PrevizDot"), Missing);
	if (!Missing.IsEmpty())
	{
		// Only possible in a build that skipped build_assets.py, or a cook that
		// left them out (DefaultGame.ini's DirectoriesToAlwaysCook).
		Problem = FString::Printf(TEXT("missing assets: %s -- rebuild with previz/build.py"), *Missing);
		UE_LOG(LogKLights, Error, TEXT("%s"), *Problem);
		return false;
	}
	return true;
}

void UKLightsSubsystem::OnWorldBeginPlay(UWorld& InWorld)
{
	Super::OnWorldBeginPlay(InWorld);
	if (!LoadAssets())
	{
		return;
	}
	bStarted = true;

	const FString Bind = Setting(TEXT("ArtNetBind"), TEXT("0.0.0.0"));
	const int32 Port = FCString::Atoi(*Setting(TEXT("ArtNetPort"), FString::FromInt(KLights::ArtNet::DefaultPort)));
	FString Error;
	if (ArtNet.Start(Bind, Port, Error))
	{
		UE_LOG(LogKLights, Display, TEXT("listening for Art-Net on %s:%d"), *Bind, Port);
	}
	else
	{
		Problem = TEXT("Art-Net: ") + Error;
		UE_LOG(LogKLights, Error, TEXT("%s"), *Problem);
	}

	const FString Cache = Setting(TEXT("ModelCache"), FString());
	if (!Cache.IsEmpty())
	{
		FKLightsEngineLink::SetCacheDir(FPaths::ConvertRelativePathToFull(Cache));
	}
	Link.OnScene = [this](const FKLightsScene& NewScene) { Rebuild(NewScene); };
	const FString File = Setting(TEXT("Scene"), FString());
	if (!File.IsEmpty())
	{
		if (!Link.LoadFile(FPaths::ConvertRelativePathToFull(File), Error))
		{
			Problem = TEXT("scene file: ") + Error;
			UE_LOG(LogKLights, Error, TEXT("%s"), *Problem);
		}
	}
	else
	{
		Link.Start(Setting(TEXT("Engine"), DefaultEngineUrl));
	}
	WindowStart = FPlatformTime::Seconds();

	SnapshotPath = Setting(TEXT("Snapshot"), FString());
	SnapshotView = Setting(TEXT("View"), TEXT("overview"));
	SnapshotDelay = FCString::Atod(*Setting(TEXT("SnapshotDelay"), TEXT("3")));
	if (!SnapshotPath.IsEmpty())
	{
		SnapshotPath = FPaths::ConvertRelativePathToFull(SnapshotPath);
	}
}

void UKLightsSubsystem::Deinitialize()
{
	Link.Stop();
	ArtNet.Stop();
	Clear();
	Super::Deinitialize();
}

void UKLightsSubsystem::Clear()
{
	for (AKLightsFixture* Fixture : Fixtures)
	{
		if (IsValid(Fixture))
		{
			Fixture->Destroy();
		}
	}
	Fixtures.Reset();
	if (IsValid(Stage))
	{
		Stage->Destroy();
	}
	Stage = nullptr;
}

const FKLightsModel* UKLightsSubsystem::GetModel(const FString& Sha, bool bCollide)
{
	if (Sha.IsEmpty())
	{
		return nullptr;
	}
	const FString Key = bCollide ? Sha + TEXT("|collide") : Sha;
	if (const TSharedPtr<FKLightsModel>* Found = Models.Find(Key))
	{
		return Found->Get();
	}
	const FString Name = Scene.IsValid() && Scene->AssetNames.Contains(Sha) ? Scene->AssetNames[Sha] : Sha.Left(12);
	const FString Path = FKLightsEngineLink::CachePath(Sha);
	TSharedPtr<FKLightsModel> Model;
	FString Error;
	if (!FPaths::FileExists(Path))
	{
		Error = TEXT("not fetched");
	}
	else
	{
		Model = MakeShared<FKLightsModel>();
		const double Started = FPlatformTime::Seconds();
		// The subsystem as Outer: it lives in the game world, which is what lets
		// a colliding mesh's trimesh cook at runtime (see ModelLoader.h).
		if (FKLightsModelLoader::Load(Path, this, SurfaceMaterial, bCollide, *Model, Error))
		{
			UE_LOG(LogKLights, Display, TEXT("loaded model %s: %d node(s), %d triangle(s) in %.0f ms"), *Name,
			       Model->Nodes.Num(), Model->Triangles, (FPlatformTime::Seconds() - Started) * 1000.0);
			for (const FString& Warning : Model->Warnings)
			{
				UE_LOG(LogKLights, Warning, TEXT("model %s: %s"), *Name, *Warning);
			}
		}
		else
		{
			Model.Reset();
		}
	}
	if (!Model.IsValid())
	{
		ModelProblems.Add(FString::Printf(TEXT("model %s: %s"), *Name, *Error));
		UE_LOG(LogKLights, Warning, TEXT("%s"), *ModelProblems.Last());
	}
	Models.Add(Key, Model);
	return Model.Get();
}

void UKLightsSubsystem::Rebuild(const FKLightsScene& NewScene)
{
	namespace Ball = KLights::Ball;
	Clear();
	Scene = MakeUnique<FKLightsScene>(NewScene);
	const FKLightsScene& S = *Scene;
	ModelProblems.Reset();
	// A model that failed last time may have been fetched since.
	for (auto It = Models.CreateIterator(); It; ++It)
	{
		if (!It->Value.IsValid())
		{
			It.RemoveCurrent();
		}
	}
	auto Model = [this](const FString& Sha, bool bCollide) { return GetModel(Sha, bCollide); };
	UWorld* World = GetWorld();

	FActorSpawnParameters Params;
	Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	Stage = World->SpawnActor<AKLightsStage>(AKLightsStage::StaticClass(), FTransform::Identity, Params);
	FKLightsStageAssets StageAssets;
	StageAssets.Cube = Cube;
	StageAssets.Sphere = Sphere;
	StageAssets.Cylinder = Cylinder;
	StageAssets.Plane = Plane;
	StageAssets.Surface = SurfaceMaterial;
	Stage->Build(S, StageAssets, Model);

	// One ball, one rotation, shared by every fixture this frame.
	const FIntPoint Lattice = Ball::Lattice(S.Ball.RadiusMm, S.Ball.ReflectSpacingMm);
	Normals = Ball::FibonacciNormals(Lattice.X * Lattice.Y);
	Spun = Normals;

	FKLightsRigContext Context;
	Context.Scene = Scene.Get();
	Context.BallCentre = S.Ball.Location;
	Context.BallRadius = S.Ball.Radius;
	Context.BallShow = Ball::PointToShow(S.Ball.Location);
	Context.RoomShow = S.RoomShowMm();
	Context.FacetCount = Normals.Num();
	Context.BallCore = Stage->GetBallCore();
	Context.Cube = Cube;
	Context.Cone = Cone;
	Context.Plane = Plane;
	Context.Surface = SurfaceMaterial;
	Context.Beam = BeamMaterial;
	Context.Dot = DotMaterial;
	Context.Model = Model;

	// Brightness ratios between fixtures, fixed for the scene's life because
	// neither the fixtures nor the ball move. The beam shafts are drawn relative
	// to the brightest beam; the ball's spray relative to whichever fixture lights
	// the ball hardest (candela over distance squared). Both compressed by
	// mesh_contrast -- see engine/scene.py OPTICS for why.
	const FKLightsOptics& O = S.Optics;
	TArray<double> Candela, Lit;
	double Brightest = 0.0, BestLit = 0.0;
	for (const FKLightsFixture& F : S.Fixtures)
	{
		const double Cd = KLights::Look::Candela(F.Lumens, F.BeamDeg);
		const double D = FMath::Max(1.0, FVector::Dist(Ball::PointToShow(F.Location), Context.BallShow));
		Candela.Add(Cd);
		Lit.Add(Cd / (D * D));
		Brightest = FMath::Max(Brightest, Cd);
		BestLit = FMath::Max(BestLit, Lit.Last());
	}
	for (int32 i = 0; i < S.Fixtures.Num(); ++i)
	{
		const FKLightsFixture& F = S.Fixtures[i];
		FKLightsShares Shares;
		Shares.BeamShare = Brightest > 0.0 ? FMath::Pow(Candela[i] / Brightest, O.MeshContrast) : 1.0;
		Shares.Illuminance = BestLit > 0.0 ? FMath::Pow(Lit[i] / BestLit, O.MeshContrast) : 1.0;
		// A wide source overshoots the ball and lights its whole near cap; sample
		// its facets coarsely enough to stay inside the budget. Fixed per fixture,
		// which is what keeps the drawn subset STABLE as the ball turns.
		const double Distance = FVector::Dist(Ball::PointToShow(F.Location), Context.BallShow);
		const int32 Want = Ball::LitFacets(Distance, F.BeamDeg / 2.0, S.Ball.RadiusMm, Normals.Num());
		const int32 Budget = FMath::Max(1, int32(O.ReflectBudget));
		Shares.Stride = FMath::Max(1, (Want + Budget - 1) / Budget);

		AKLightsFixture* Actor = World->SpawnActor<AKLightsFixture>(AKLightsFixture::StaticClass(),
		                                                            FTransform(F.RestRotation, F.Location), Params);
		Actor->Setup(F, Context, Shares);
		Fixtures.Add(Actor);
	}

	UE_LOG(LogKLights, Display, TEXT("built %s in %s: %d fixture(s), %d universe(s), ball %d facets, scene %s"),
	       *S.Event, *S.Venue, Fixtures.Num(), S.Universes.Num(), Normals.Num(), *S.Rev);
	for (const FString& Warning : S.Warnings)
	{
		UE_LOG(LogKLights, Warning, TEXT("scene: %s"), *Warning);
	}
	for (const FString& Name : S.Unplaced)
	{
		UE_LOG(LogKLights, Warning, TEXT("not placed (no position in rig.json): %s"), *Name);
	}

	if (!bCameraPlaced)
	{
		if (APlayerController* PC = World->GetFirstPlayerController())
		{
			GoToView(0, PC);
			bCameraPlaced = true;
		}
	}
	else if (S.Views.IsValidIndex(View))
	{
		Stage->ApplyView(S.Views[View]);
	}
}

void UKLightsSubsystem::Tick(float DeltaTime)
{
	Super::Tick(DeltaTime);
	if (!bStarted)
	{
		return;
	}
	const double Now = FPlatformTime::Seconds();
	FrameMs = FrameMs <= 0.0 ? DeltaTime * 1000.0 : FMath::Lerp(FrameMs, DeltaTime * 1000.0, 0.05);
	Link.Tick(Now);
	WindowPackets += ArtNet.Drain();
	if (Now - WindowStart >= 1.0)
	{
		PacketsPerSecond = WindowPackets / (Now - WindowStart);
		WindowPackets = 0;
		WindowStart = Now;
	}
	if (!Scene.IsValid())
	{
		return;
	}
	if (!bCameraPlaced)
	{
		if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
		{
			GoToView(0, PC);
			bCameraPlaced = true;
		}
	}

	// The ball turns: rpm * 6 is degrees per second.
	BallAngle = FMath::Fmod(BallAngle + DeltaTime * Scene->Ball.Rpm * 6.0, 360.0);
	KLights::Ball::Spin(Normals, BallAngle, Spun);
	Stage->SpinBall(BallAngle);

	for (AKLightsFixture* Fixture : Fixtures)
	{
		Fixture->Drive(ArtNet.Frame(Fixture->GetFixture().Universe), DeltaTime, Spun);
	}
	if (!SnapshotPath.IsEmpty())
	{
		TickSnapshot(Now);
	}
}

void UKLightsSubsystem::TickSnapshot(double Now)
{
	if (QuitAt > 0.0)
	{
		if (Now >= QuitAt)
		{
			UE_LOG(LogKLights, Display, TEXT("snapshot written: %s"), *SnapshotPath);
			QuitAt = -1.0;
			FPlatformMisc::RequestExit(false, TEXT("KLightsSnapshot"));
		}
		return;
	}
	if (SnapshotAt == 0.0)
	{
		SnapshotAt = Now + SnapshotDelay;   // counted from the scene arriving
		return;
	}
	if (Now < SnapshotAt)
	{
		return;
	}
	if (SnapshotFrame == 0)
	{
		// A still asks what the look IS, not where four heads have crept to a
		// third of the way through a travel -- so finish every move first, and
		// give everything hanging off the yokes a few frames to follow.
		SettleAll();
		const int32 Index = Scene->Views.IndexOfByPredicate([this](const FKLightsView& V) { return V.Name == SnapshotView; });
		APlayerController* PC = GetWorld()->GetFirstPlayerController();
		GoToView(FMath::Max(0, Index), PC);
		// -At=X,Y,Z -LookAt=X,Y,Z (Unreal cm): stand somewhere the scene's own
		// views do not, keeping the named view's fog and field of view.
		FVector At, LookAt;
		auto Parse = [](const TCHAR* Key, FVector& Out)
		{
			FString Text;
			TArray<FString> Parts;
			// bShouldStopOnSeparator off: the value IS comma-separated.
			if (!FParse::Value(FCommandLine::Get(), Key, Text, false) || Text.ParseIntoArray(Parts, TEXT(",")) != 3)
			{
				return false;
			}
			Out = FVector(FCString::Atod(*Parts[0]), FCString::Atod(*Parts[1]), FCString::Atod(*Parts[2]));
			return true;
		};
		if (PC != nullptr && Parse(TEXT("At="), At) && Parse(TEXT("LookAt="), LookAt))
		{
			if (APawn* Pawn = PC->GetPawn())
			{
				Pawn->SetActorLocation(At, false, nullptr, ETeleportType::TeleportPhysics);
			}
			PC->SetControlRotation((LookAt - At).Rotation());
		}
		// The overlay is drawn into the frame, not over it, so a still has to ask.
		if (AKLightsHUD* Hud = PC ? Cast<AKLightsHUD>(PC->GetHUD()) : nullptr)
		{
			Hud->bShowOverlay = FParse::Param(FCommandLine::Get(), TEXT("SnapshotHud"));
		}
		SnapshotFrame = GFrameCounter + 30;
		return;
	}
	if (GFrameCounter >= SnapshotFrame)
	{
		FScreenshotRequest::RequestScreenshot(SnapshotPath, /*bShowUI=*/ false, /*bAddFilenameSuffix=*/ false);
		UE_LOG(LogKLights, Display, TEXT("frame time at the snapshot: %.1f ms (%.0f fps)"), FrameMs,
		       FrameMs > 0.0 ? 1000.0 / FrameMs : 0.0);
		QuitAt = Now + 1.5;
	}
}

void UKLightsSubsystem::GoToView(int32 Index, APlayerController* Controller)
{
	if (!Scene.IsValid() || !Scene->Views.IsValidIndex(Index) || Controller == nullptr)
	{
		return;
	}
	View = Index;
	const FKLightsView& V = Scene->Views[Index];
	if (APawn* Pawn = Controller->GetPawn())
	{
		Pawn->SetActorLocation(V.Location, false, nullptr, ETeleportType::TeleportPhysics);
	}
	Controller->SetControlRotation((V.Target - V.Location).Rotation());
	if (Controller->PlayerCameraManager != nullptr)
	{
		Controller->PlayerCameraManager->SetFOV(float(V.Fov));
	}
	if (Stage != nullptr)
	{
		Stage->ApplyView(V);
	}
}

void UKLightsSubsystem::SettleAll()
{
	for (AKLightsFixture* Fixture : Fixtures)
	{
		Fixture->Settle();
	}
}

int32 UKLightsSubsystem::GetMovingHeads() const
{
	int32 Moving = 0;
	for (const AKLightsFixture* Fixture : Fixtures)
	{
		Moving += Fixture->IsMoving() ? 1 : 0;
	}
	return Moving;
}
