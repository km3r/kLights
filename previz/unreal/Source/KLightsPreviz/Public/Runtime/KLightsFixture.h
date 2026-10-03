#pragma once

#include "CoreMinimal.h"
#include "Core/Decode.h"
#include "Core/Optics.h"
#include "GameFramework/Actor.h"
#include "Runtime/ModelLoader.h"
#include "Runtime/Scene.h"
#include "KLightsFixture.generated.h"

class UInstancedStaticMeshComponent;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class UPointLightComponent;
class USpotLightComponent;
class UStaticMesh;
class UStaticMeshComponent;

/** What every fixture shares, worked out once per scene by the subsystem. */
struct FKLightsRigContext
{
	const FKLightsScene* Scene = nullptr;
	FVector BallCentre = FVector::ZeroVector;   // Unreal cm
	double BallRadius = 0.0;                     // Unreal cm
	FVector BallShow = FVector::ZeroVector;      // show frame, mm
	FVector RoomShow = FVector::ZeroVector;      // show frame, mm
	int32 FacetCount = 0;
	/** Actors a beam's trace passes through when the ball only clips it. */
	TArray<const AActor*> IgnoreWithBall;
	const UPrimitiveComponent* BallCore = nullptr;
	UStaticMesh* Cube = nullptr;
	UStaticMesh* Cone = nullptr;
	UStaticMesh* Plane = nullptr;
	UMaterialInterface* Surface = nullptr;
	UMaterialInterface* Beam = nullptr;
	UMaterialInterface* Dot = nullptr;
	/** The scene's models by hash; null when one is missing. */
	TFunction<const FKLightsModel*(const FString& Sha, bool bCollide)> Model;
};

/** Per-fixture numbers the subsystem derives from the whole rig. */
struct FKLightsShares
{
	double BeamShare = 1.0;     // the shaft's brightness, relative to the brightest beam
	double Illuminance = 1.0;   // how brightly it lights the ball, relative to the best
	int32 Stride = 1;           // mirror-ball facet sampling
};

/**
 * One patched unit in the room: its light (two, for a split colour wheel), the
 * shaft that stands in for its beam, the glow the mirror ball throws back, the
 * ball's reflections of it, and a body.
 *
 * Driven once a frame from the DMX it is patched to. A mover's aim is decoded
 * through the show's own calibration (Decode.h, held to the Python by the
 * parity tests); a fixed fixture points where its bracket does.
 */
UCLASS()
class KLIGHTSPREVIZ_API AKLightsFixture : public AActor
{
	GENERATED_BODY()

public:
	AKLightsFixture();

	void Setup(const FKLightsFixture& InFixture, const FKLightsRigContext& InContext, const FKLightsShares& InShares);

	/** One frame. `Frame` is this fixture's universe, null if never heard. */
	void Drive(const uint8* Frame, double Dt, const TArray<FVector>& SpunNormals);

	/** Finish every move now. For stills. */
	void Settle() { Servo.Settle(); }

	const FKLightsFixture& GetFixture() const { return Fixture; }
	bool IsMoving() const { return Fixture.bMover && !Servo.Arrived(); }

private:
	void LightUp(const FRotator& Aim, const KLights::FOutput& Out);
	void AimBeam(const FVector& Origin, const FVector& Direction, const KLights::FOutput& Out);
	void BallGlow(double Caught, const FVector& Color);
	void PlaceReflections(const FVector& Origin, const FVector& Direction, const KLights::FOutput& Out,
	                      const TArray<FVector>& SpunNormals);
	USpotLightComponent* MakeSpot(const TCHAR* Name);
	void BuildBoxBody();
	void BuildModelBody(const FKLightsModel& Model);
	void PoseBody(const FVector& Direction);

	FKLightsFixture Fixture;
	FKLightsRigContext Context;
	FKLightsShares Shares;
	KLights::FServo Servo;

	UPROPERTY() TObjectPtr<USceneComponent> Root;
	UPROPERTY() TObjectPtr<USpotLightComponent> Spot;
	UPROPERTY() TObjectPtr<USpotLightComponent> SpotHalf;    // the other half of a split wheel
	UPROPERTY() TObjectPtr<UStaticMeshComponent> Shaft;
	UPROPERTY() TObjectPtr<UMaterialInstanceDynamic> ShaftMaterial;
	UPROPERTY() TObjectPtr<UPointLightComponent> Glow;
	UPROPERTY() TObjectPtr<UInstancedStaticMeshComponent> Dots;
	UPROPERTY() TObjectPtr<UInstancedStaticMeshComponent> Rays;
	UPROPERTY() TObjectPtr<UMaterialInstanceDynamic> DotMaterial;
	UPROPERTY() TObjectPtr<UMaterialInstanceDynamic> RayMaterial;
	UPROPERTY() TObjectPtr<UStaticMeshComponent> Body;     // the stand-in box
	UPROPERTY() TObjectPtr<USceneComponent> BodyRoot;      // a model body
	UPROPERTY() TObjectPtr<USceneComponent> Yoke;
	UPROPERTY() TObjectPtr<USceneComponent> HeadPart;
	/** The frame the yoke pans in: the base's mount, times any rotation above the yoke. */
	FQuat PanFrame = FQuat::Identity;
	double BodyPan = 0.0;

	int32 Capacity = 0;
	int32 DotsShown = 0;
	int32 RaysShown = 0;
	FVector LastLookColor = FVector(-1.0);
	double LastLookLevel = -1.0;
	TArray<KLights::Ball::FDot> Hits;
	TArray<FTransform> DotTransforms;
	TArray<FTransform> RayTransforms;
};
