#include "Spike/KLightsSpikeGameMode.h"

#include "Camera/CameraActor.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "Engine/PointLight.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "KLightsLog.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"

namespace
{
	const TCHAR* ModelMaterial = TEXT("/Game/Previz/Materials/M_PrevizModel.M_PrevizModel");
	const TCHAR* DotMaterial = TEXT("/Game/Previz/Materials/M_PrevizDot.M_PrevizDot");
	const TCHAR* PlaneMesh = TEXT("/Engine/BasicShapes/Plane.Plane");
}

AKLightsSpikeGameMode::AKLightsSpikeGameMode()
{
	PrimaryActorTick.bCanEverTick = true;
}

void AKLightsSpikeGameMode::BeginPlay()
{
	Super::BeginPlay();
	StartedAt = ReportAt = FPlatformTime::Seconds();
	FParse::Value(FCommandLine::Get(), TEXT("SpikeSeconds="), Seconds);
	FParse::Value(FCommandLine::Get(), TEXT("SpikeShot="), ShotPath);

	// Inside the despacio room, clear of the ball and the rig.
	Site = FVector(500.0, 914.0, 120.0);

	FString Error;
	if (ArtNet.Start(TEXT("0.0.0.0"), KLights::ArtNet::DefaultPort, Error))
	{
		UE_LOG(LogKLights, Display, TEXT("SPIKE d listening on 0.0.0.0:%d"), KLights::ArtNet::DefaultPort);
	}
	else
	{
		UE_LOG(LogKLights, Error, TEXT("SPIKE d FAIL %s"), *Error);
	}

	FString Url;
	if (FParse::Value(FCommandLine::Get(), TEXT("SpikeGlb="), Url))
	{
		Fetch(Url);
	}
	Instanced();
}

void AKLightsSpikeGameMode::EndPlay(const EEndPlayReason::Type Reason)
{
	ArtNet.Stop();
	Super::EndPlay(Reason);
}

void AKLightsSpikeGameMode::Fetch(const FString& Url)
{
	TSharedRef<IHttpRequest, ESPMode::ThreadSafe> Request = FHttpModule::Get().CreateRequest();
	Request->SetURL(Url);
	Request->SetVerb(TEXT("GET"));
	Request->SetTimeout(10.f);
	Request->OnProcessRequestComplete().BindWeakLambda(this,
		[this, Url](FHttpRequestPtr, FHttpResponsePtr Response, bool bConnected)
		{
			if (!bConnected || !Response.IsValid() || Response->GetResponseCode() != 200)
			{
				UE_LOG(LogKLights, Error, TEXT("SPIKE a FAIL fetch %s (connected=%d code=%d)"), *Url,
				       bConnected, Response.IsValid() ? Response->GetResponseCode() : 0);
				return;
			}
			const FString Path = FPaths::ProjectSavedDir() / TEXT("ModelCache") / TEXT("spike.glb");
			FFileHelper::SaveArrayToFile(Response->GetContent(), *Path);
			UE_LOG(LogKLights, Display, TEXT("SPIKE a fetched %d bytes -> %s"), Response->GetContent().Num(), *Path);
			Place(Path);
		});
	Request->ProcessRequest();
}

void AKLightsSpikeGameMode::Place(const FString& GlbPath)
{
	UMaterialInterface* Base = LoadObject<UMaterialInterface>(nullptr, ModelMaterial);
	if (Base == nullptr)
	{
		UE_LOG(LogKLights, Error, TEXT("SPIKE a FAIL %s did not load (not cooked?)"), ModelMaterial);
		return;
	}
	FString Error;
	const double T0 = FPlatformTime::Seconds();
	// `this` as the Outer: a game-world actor, so runtime trimesh cooking runs.
	if (!FKLightsModelLoader::Load(GlbPath, this, Base, /*bCollide=*/ true, Model, Error))
	{
		UE_LOG(LogKLights, Error, TEXT("SPIKE a FAIL load: %s"), *Error);
		return;
	}
	for (const FString& Warning : Model.Warnings)
	{
		UE_LOG(LogKLights, Warning, TEXT("SPIKE a model warning: %s"), *Warning);
	}

	ModelActor = GetWorld()->SpawnActor<AActor>(AActor::StaticClass(), FTransform(Site));
	USceneComponent* Root = NewObject<USceneComponent>(ModelActor, TEXT("Root"));
	Root->SetMobility(EComponentMobility::Movable);
	ModelActor->SetRootComponent(Root);
	Root->RegisterComponent();
	ModelActor->SetActorLocation(Site);
	TMap<FString, USceneComponent*> ByName;
	FKLightsModelLoader::Instantiate(Model, ModelActor, Root, /*bCollide=*/ true, ByName);
	UE_LOG(LogKLights, Display, TEXT("SPIKE a PASS %d nodes, %d triangles, %.1f ms"),
	       Model.Nodes.Num(), Model.Triangles, (FPlatformTime::Seconds() - T0) * 1000.0);

	// Where each arm ended up, so the axis mapping can be read off the log.
	for (const TPair<FString, USceneComponent*>& Pair : ByName)
	{
		const FBox Box = Pair.Value->Bounds.GetBox();
		UE_LOG(LogKLights, Display, TEXT("SPIKE a node %-8s bounds centre (rel) %s"), *Pair.Key,
		       *(Box.GetCenter() - Site).ToString());
	}
	bPlaced = true;
	Frame();
}

void AKLightsSpikeGameMode::Trace()
{
	// Down onto the cube's top face, off the axis arms.
	const FVector Start = Site + FVector(-15.0, -15.0, 300.0);
	const FVector End = Site + FVector(-15.0, -15.0, -50.0);
	FHitResult Hit;
	const bool bHit = GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility);
	if (bHit && Hit.GetActor() == ModelActor)
	{
		UE_LOG(LogKLights, Display, TEXT("SPIKE b PASS hit %s at z=%.2f (cube top expected at %.2f)"),
		       *GetNameSafe(Hit.GetComponent()), Hit.ImpactPoint.Z, Site.Z + 25.0);
	}
	else
	{
		UE_LOG(LogKLights, Error, TEXT("SPIKE b FAIL hit=%d actor=%s"), bHit, *GetNameSafe(Hit.GetActor()));
	}
}

void AKLightsSpikeGameMode::Instanced()
{
	UStaticMesh* Plane = LoadObject<UStaticMesh>(nullptr, PlaneMesh);
	UMaterialInterface* Dot = LoadObject<UMaterialInterface>(nullptr, DotMaterial);
	if (Plane == nullptr || Dot == nullptr)
	{
		UE_LOG(LogKLights, Error, TEXT("SPIKE c FAIL plane=%d dot=%d (not cooked?)"), Plane != nullptr, Dot != nullptr);
		return;
	}
	const bool bFlag = Dot->GetMaterial()->GetUsageByFlag(MATUSAGE_InstancedStaticMeshes);
	UE_LOG(LogKLights, Display, TEXT("SPIKE c %s M_PrevizDot used-with-instanced-static-meshes=%d"),
	       bFlag ? TEXT("PASS") : TEXT("FAIL"), bFlag);

	AActor* Holder = GetWorld()->SpawnActor<AActor>(AActor::StaticClass(), FTransform(Site));
	USceneComponent* Root = NewObject<USceneComponent>(Holder, TEXT("Root"));
	Holder->SetRootComponent(Root);
	Root->RegisterComponent();
	UInstancedStaticMeshComponent* Dots = NewObject<UInstancedStaticMeshComponent>(Holder, TEXT("Dots"));
	Dots->SetStaticMesh(Plane);
	UMaterialInstanceDynamic* MID = UMaterialInstanceDynamic::Create(Dot, Holder);
	MID->SetVectorParameterValue(TEXT("Color"), FLinearColor(0.1f, 1.f, 0.2f));
	MID->SetScalarParameterValue(TEXT("Brightness"), 20.f);
	Dots->SetMaterial(0, MID);
	Dots->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Dots->SetupAttachment(Root);
	Dots->RegisterComponent();
	// A row of upright quads beside the model, facing the camera (-X).
	for (int32 i = 0; i < 5; ++i)
	{
		const FVector At = Site + FVector(0.0, 120.0 + i * 45.0, 60.0 + (i % 2) * 40.0);
		Dots->AddInstance(FTransform(FRotator(90.0, 0.0, 0.0), At, FVector(0.35)), /*bWorldSpace=*/ true);
	}
}

void AKLightsSpikeGameMode::Frame()
{
	ACameraActor* Camera = GetWorld()->SpawnActor<ACameraActor>(
		Site + FVector(-330.0, -150.0, 120.0), FRotator::ZeroRotator);
	Camera->SetActorRotation((Site + FVector(0.0, 80.0, 30.0) - Camera->GetActorLocation()).Rotation());
	APointLight* Light = GetWorld()->SpawnActor<APointLight>(Site + FVector(-200.0, -250.0, 250.0), FRotator::ZeroRotator);
	Light->PointLightComponent->SetIntensityUnits(ELightUnits::Lumens);
	Light->PointLightComponent->SetIntensity(3000.f);
	Light->PointLightComponent->SetAttenuationRadius(2000.f);
	if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
	{
		PC->SetViewTarget(Camera);
	}
}

void AKLightsSpikeGameMode::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	ArtNet.Drain();
	const double Now = FPlatformTime::Seconds();

	if (bPlaced && !bTraced)
	{
		bTraced = true;
		Trace();
	}
	if (Now >= ReportAt + 2.0)
	{
		ReportAt = Now;
		TArray<int32> Seen;
		ArtNet.Frames().GetKeys(Seen);
		const uint8* U0 = ArtNet.Frame(0);
		UE_LOG(LogKLights, Display, TEXT("SPIKE d %s packets=%llu universes=[%s] u0[0..7]=%s"),
		       ArtNet.PacketsTotal > 0 ? TEXT("PASS") : TEXT("waiting"), ArtNet.PacketsTotal,
		       *FString::JoinBy(Seen, TEXT(","), [](int32 U) { return FString::FromInt(U); }),
		       U0 ? *FString::Printf(TEXT("%d %d %d %d %d %d %d %d"), U0[0], U0[1], U0[2], U0[3], U0[4], U0[5], U0[6], U0[7]) : TEXT("-"));
	}
	if (!bShot && Now - StartedAt >= Seconds)
	{
		bShot = true;
		if (!ShotPath.IsEmpty())
		{
			FScreenshotRequest::RequestScreenshot(ShotPath, /*bShowUI=*/ false, /*bAddFilenameSuffix=*/ false);
			UE_LOG(LogKLights, Display, TEXT("SPIKE e screenshot requested -> %s"), *ShotPath);
		}
		QuitAt = Now + 2.0;
	}
	if (QuitAt > 0.0 && Now >= QuitAt)
	{
		UE_LOG(LogKLights, Display, TEXT("SPIKE done"));
		FPlatformMisc::RequestExit(false, TEXT("KLightsSpike"));
		QuitAt = -1.0;
	}
}
