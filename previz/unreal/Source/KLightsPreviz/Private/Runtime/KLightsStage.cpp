#include "Runtime/KLightsStage.h"

#include "Components/ExponentialHeightFogComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/PostProcessComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Core/Optics.h"
#include "Materials/MaterialInstanceDynamic.h"

namespace
{
	// The basic shapes are 100 cm across at scale 1.
	constexpr double ShapeCm = 100.0;
	// Walls, floor and ceiling.
	constexpr double WallCm = 10.0;
}

AKLightsStage::AKLightsStage()
{
	PrimaryActorTick.bCanEverTick = false;
	Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	RootComponent = Root;
}

UStaticMeshComponent* AKLightsStage::Shape(const TCHAR* Name, UStaticMesh* Mesh, const FVector& Location,
                                           const FVector& Scale, const FLinearColor& Color, float Roughness,
                                           float Metallic, float Emissive)
{
	UStaticMeshComponent* C = NewObject<UStaticMeshComponent>(this, Name);
	C->SetupAttachment(Root);
	C->SetMobility(EComponentMobility::Movable);
	C->SetStaticMesh(Mesh);
	UMaterialInstanceDynamic* M = UMaterialInstanceDynamic::Create(Assets.Surface, this);
	M->SetVectorParameterValue(TEXT("BaseColor"), Color);
	M->SetScalarParameterValue(TEXT("Roughness"), Roughness);
	M->SetScalarParameterValue(TEXT("Metallic"), Metallic);
	M->SetVectorParameterValue(TEXT("Emissive"), Color * Emissive);
	C->SetMaterial(0, M);
	C->SetRelativeLocation(Location);
	C->SetRelativeScale3D(Scale);
	C->RegisterComponent();
	AddInstanceComponent(C);
	return C;
}

void AKLightsStage::Build(const FKLightsScene& Scene, const FKLightsStageAssets& InAssets, const FModelSource& Model)
{
	Assets = InAssets;
	const FKLightsOptics& O = Scene.Optics;
	const FVector Size = Scene.RoomSize;   // X = depth, Y = width, Z = height
	const double CX = Size.X / 2.0, CY = Size.Y / 2.0;
	const double T = WallCm / ShapeCm;

	// Dark but not black, and faintly self-lit. A sky light inside a closed box
	// captures its own black walls and adds nothing, so without the emissive an
	// unlit wall cannot be told from no wall at all.
	const float Albedo = float(O.RoomAlbedo);
	const FLinearColor Room(Albedo, Albedo, Albedo * 1.15f, 1.f);
	const float RoomEmissive = float(O.RoomEmissive);
	bWalls = Scene.bWalls;
	TArray<UStaticMeshComponent*> Box;
	Box.Add(Shape(TEXT("Floor"), Assets.Cube, FVector(CX, CY, -WallCm / 2.0),
	              FVector(Size.X / ShapeCm, Size.Y / ShapeCm, T), Room, 0.85f, 0.f, RoomEmissive));
	Ceiling = Shape(TEXT("Ceiling"), Assets.Cube, FVector(CX, CY, Size.Z + WallCm / 2.0),
	                FVector(Size.X / ShapeCm, Size.Y / ShapeCm, T), Room, 0.85f, 0.f, RoomEmissive);
	Box.Add(Ceiling);
	Box.Add(Shape(TEXT("WallFront"), Assets.Cube, FVector(-WallCm / 2.0, CY, Size.Z / 2.0),
	              FVector(T, Size.Y / ShapeCm, Size.Z / ShapeCm), Room, 0.85f, 0.f, RoomEmissive));
	Box.Add(Shape(TEXT("WallBack"), Assets.Cube, FVector(Size.X + WallCm / 2.0, CY, Size.Z / 2.0),
	              FVector(T, Size.Y / ShapeCm, Size.Z / ShapeCm), Room, 0.85f, 0.f, RoomEmissive));
	Box.Add(Shape(TEXT("WallLeft"), Assets.Cube, FVector(CX, -WallCm / 2.0, Size.Z / 2.0),
	              FVector(Size.X / ShapeCm, T, Size.Z / ShapeCm), Room, 0.85f, 0.f, RoomEmissive));
	Box.Add(Shape(TEXT("WallRight"), Assets.Cube, FVector(CX, Size.Y + WallCm / 2.0, Size.Z / 2.0),
	              FVector(Size.X / ShapeCm, T, Size.Z / ShapeCm), Room, 0.85f, 0.f, RoomEmissive));
	if (!bWalls)
	{
		// A venue model brings its own walls. The box goes, collision and all;
		// the mirror ball's dots still land on its analytic planes.
		for (UStaticMeshComponent* C : Box)
		{
			C->SetVisibility(false);
			C->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		}
	}

	// The mirror ball: a solid dark core -- the occluding sphere the safety taper
	// models, so it casts real shadows and stops beams -- wearing mirrors.
	const FKLightsBall& B = Scene.Ball;
	BallCore = Shape(TEXT("BallCore"), Assets.Sphere, B.Location, FVector(2.0 * B.Radius / ShapeCm),
	                 FLinearColor(0.02f, 0.02f, 0.025f), 0.9f, 0.f, 0.f);
	BallCore->SetCastShadow(true);

	BallPivot = NewObject<USceneComponent>(this, TEXT("BallPivot"));
	BallPivot->SetupAttachment(Root);
	BallPivot->SetMobility(EComponentMobility::Movable);
	BallPivot->SetRelativeLocation(B.Location);
	BallPivot->RegisterComponent();
	AddInstanceComponent(BallPivot);

	BallTiles = NewObject<UInstancedStaticMeshComponent>(this, TEXT("BallTiles"));
	BallTiles->SetupAttachment(BallPivot);
	BallTiles->SetMobility(EComponentMobility::Movable);
	BallTiles->SetStaticMesh(Assets.Plane);
	// Fully metallic, no diffuse at all: give these any and two 1300 lm beams
	// blow the lit hemisphere into one white blob. A mirror is dark except where
	// it is aimed at you. The emissive is the floor a black room cannot capture away.
	UMaterialInstanceDynamic* Mirror = UMaterialInstanceDynamic::Create(Assets.Surface, this);
	const FLinearColor Silver(0.9f, 0.92f, 0.97f);
	Mirror->SetVectorParameterValue(TEXT("BaseColor"), Silver);
	Mirror->SetScalarParameterValue(TEXT("Roughness"), 0.18f);
	Mirror->SetScalarParameterValue(TEXT("Metallic"), 1.f);
	Mirror->SetVectorParameterValue(TEXT("Emissive"), Silver * 0.14f);
	BallTiles->SetMaterial(0, Mirror);
	BallTiles->SetCastShadow(false);
	BallTiles->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	BallTiles->RegisterComponent();
	AddInstanceComponent(BallTiles);
	{
		namespace Ball = KLights::Ball;
		// The lattice comes from the radius in mm (it is a facet SIZE in mm); the
		// tiles are then built in Unreal cm.
		const FIntPoint Lattice = Ball::Lattice(B.RadiusMm, B.MirrorSpacingMm);
		const double Seat = B.Radius + B.TileLiftCm;   // ON the core, not in it
		TArray<FTransform> Tiles;
		for (const Ball::FTile& Tile : Ball::FacetTiles(B.Radius, Lattice.X, Lattice.Y))
		{
			const FVector Out = Ball::DirectionToUnreal(Tile.Normal);
			const FVector East = Ball::DirectionToUnreal(Tile.East);
			// Local X along the ring and local Z out of the ball.
			Tiles.Emplace(FRotationMatrix::MakeFromXZ(East, Out).Rotator(), Out * Seat,
			              FVector(Tile.Width * B.TileCoverage / ShapeCm, Tile.Height * B.TileCoverage / ShapeCm, 1.0));
		}
		BallTiles->AddInstances(Tiles, false, /*bWorldSpace=*/ false);
	}

	// A ball model REPLACES the drawn tiles, and turns with them. The core stays:
	// it is the occluder the beams and the safety taper both stop on.
	if (const FKLightsModel* BallModel = Model(B.Model, false))
	{
		BallTiles->SetVisibility(false);
		TMap<FString, USceneComponent*> Unused;
		FKLightsModelLoader::Instantiate(*BallModel, this, BallPivot, false, Unused);
	}

	// Set pieces and venue architecture. They collide unless told otherwise: a
	// beam aimed at a wall that is in the model really does stop there.
	for (const FKLightsPlacedModel& Placed : Scene.Models)
	{
		const FKLightsModel* Loaded = Model(Placed.Model, Placed.bCollide);
		if (Loaded == nullptr)
		{
			continue;   // reported by the subsystem; the room still appears
		}
		USceneComponent* Holder = NewObject<USceneComponent>(this, *(TEXT("Model_") + Placed.Name));
		Holder->SetupAttachment(Root);
		Holder->SetMobility(EComponentMobility::Movable);
		// A venue model is authored standing at the room's front looking in -- +X
		// across its width, +Y up, receding along -Z (in Blender: X, then Y away
		// from you, Z up) -- with its origin at the front-left floor corner, the
		// same corner the venue file measures from. GLTFCore lands that frame in
		// Unreal mirrored relative to the show's (it swaps Y and Z; the show's
		// mapping cycles them), and a quarter turn about Z is exactly the
		// difference. See docs/models.md.
		const FQuat Venue = Placed.Rotation.Quaternion() * FRotator(0.0, 90.0, 0.0).Quaternion();
		Holder->SetRelativeTransform(FTransform(Venue, Placed.Location, FVector(Placed.Scale)));
		Holder->RegisterComponent();
		AddInstanceComponent(Holder);
		TMap<FString, USceneComponent*> Unused;
		FKLightsModelLoader::Instantiate(*Loaded, this, Holder, Placed.bCollide, Unused);
	}

	// The frame the rig hangs on. Collision ON: a beam aimed into steel stops
	// there. No shadows: a thin bright bar across a beam throws a hard stripe
	// for no benefit. Not metallic, though it is metal -- a metal surface shows
	// its surroundings, and its surroundings are a black room.
	const FLinearColor Aluminium(0.30f, 0.31f, 0.34f);
	for (const FKLightsBar& Bar : Scene.Truss)
	{
		UStaticMeshComponent* C = Shape(*(TEXT("Truss_") + Bar.Label), Assets.Cube, Bar.Center,
		                                2.0 * Bar.Extent / ShapeCm, Aluminium, 0.5f, 0.2f, RoomEmissive);
		C->SetCastShadow(false);
	}

	// The parachute: both a throw limit and a target that glows when hit.
	if (Scene.bHasCanopy)
	{
		Shape(TEXT("Canopy"), Assets.Cylinder, Scene.CanopyLocation,
		      FVector(2.0 * Scene.CanopyRadius / ShapeCm, 2.0 * Scene.CanopyRadius / ShapeCm, 0.05),
		      FLinearColor(0.75f, 0.72f, 0.7f), 0.95f, 0.f, 0.f);
	}

	// Haze: the entire reason a beam is visible in flight. Even floor to ceiling
	// (near-zero falloff), slices spread to just past the room's far corner.
	Fog = NewObject<UExponentialHeightFogComponent>(this, TEXT("Haze"));
	Fog->SetupAttachment(Root);
	Fog->SetRelativeLocation(FVector(CX, CY, 0.0));
	Fog->FogDensity = float(O.FogDensity);
	Fog->FogHeightFalloff = float(O.FogHeightFalloff);
	Fog->bEnableVolumetricFog = true;
	Fog->VolumetricFogExtinctionScale = 1.f;
	Fog->VolumetricFogDistance = float(Scene.MaxThrow * 1.1);
	Fog->VolumetricFogStartDistance = 0.f;
	Fog->VolumetricFogScatteringDistribution = float(O.FogScatteringDistribution);
	Fog->VolumetricFogAlbedo = FColor::White;
	Fog->RegisterComponent();
	AddInstanceComponent(Fog);

	// Pinned exposure, and bloom. A previz whose exposure drifts cannot be
	// compared shot to shot, and a dark room is exactly what auto-exposure
	// exists to destroy. Min = max is the idiom for "do not adapt".
	Look = NewObject<UPostProcessComponent>(this, TEXT("Look"));
	Look->SetupAttachment(Root);
	Look->bUnbound = true;
	Look->Priority = 1.f;
	FPostProcessSettings& S = Look->Settings;
	S.bOverride_AutoExposureMethod = true;
	S.AutoExposureMethod = AEM_Histogram;
	S.bOverride_AutoExposureMinBrightness = true;
	S.AutoExposureMinBrightness = float(O.Exposure);
	S.bOverride_AutoExposureMaxBrightness = true;
	S.AutoExposureMaxBrightness = float(O.Exposure);
	S.bOverride_AutoExposureBias = true;
	S.AutoExposureBias = 0.f;
	// Bloom is what makes a beam whose core is blown out still read as a beam.
	S.bOverride_BloomIntensity = true;
	S.BloomIntensity = float(O.BloomIntensity);
	S.bOverride_BloomThreshold = true;
	S.BloomThreshold = float(O.BloomThreshold);
	Look->RegisterComponent();
	AddInstanceComponent(Look);

	// Just enough ambient to read the room's shape with every fixture dark, and
	// none of it in the haze, or the room fills with an even glow.
	Sky = NewObject<USkyLightComponent>(this, TEXT("Ambient"));
	Sky->SetupAttachment(Root);
	Sky->SetMobility(EComponentMobility::Movable);
	Sky->SetRelativeLocation(FVector(CX, CY, Size.Z / 2.0));
	Sky->Intensity = float(O.SkyLight);
	Sky->VolumetricScatteringIntensity = 0.f;
	Sky->RegisterComponent();
	AddInstanceComponent(Sky);
	Sky->RecaptureSky();
}

void AKLightsStage::SpinBall(double AngleDeg)
{
	// The reflections turn the show frame's normals about its vertical; in
	// Unreal's axes that is a yaw of MINUS the angle (see Ball::PointToUnreal).
	if (BallPivot != nullptr)
	{
		BallPivot->SetRelativeRotation(FRotator(0.0, -AngleDeg, 0.0));
	}
}

void AKLightsStage::ApplyView(const FKLightsView& View)
{
	if (Ceiling != nullptr && bWalls)
	{
		// Hidden, not removed: a beam still stops on it.
		Ceiling->SetVisibility(!View.bHideCeiling);
	}
	if (Fog != nullptr)
	{
		// Measured FROM THE CAMERA, so it belongs to the view, not the room.
		Fog->VolumetricFogStartDistance = float(View.FogStart);
		Fog->MarkRenderStateDirty();
	}
}

const UPrimitiveComponent* AKLightsStage::GetBallCore() const
{
	return BallCore;
}
