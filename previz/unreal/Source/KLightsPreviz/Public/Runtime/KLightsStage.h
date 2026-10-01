#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "Runtime/Scene.h"
#include "KLightsStage.generated.h"

class UExponentialHeightFogComponent;
class UInstancedStaticMeshComponent;
class UMaterialInterface;
class UPostProcessComponent;
class USkyLightComponent;
class UStaticMesh;
class UStaticMeshComponent;

/** The meshes and materials the stage is built from, loaded once. */
struct FKLightsStageAssets
{
	UStaticMesh* Cube = nullptr;
	UStaticMesh* Sphere = nullptr;
	UStaticMesh* Cylinder = nullptr;
	UStaticMesh* Plane = nullptr;
	UMaterialInterface* Surface = nullptr;
};

/**
 * Everything in the room that is not a fixture: the room box, the truss, the
 * canopy, the mirror ball, the haze, the pinned exposure and the faint sky
 * light. What the editor builder used to place as level actors, built at
 * runtime from the engine's scene instead.
 *
 * The room is a closed dark box on purpose: an open one leaks the sky light in
 * and lifts the black floor, and the beams stop reading against the walls.
 */
UCLASS()
class KLIGHTSPREVIZ_API AKLightsStage : public AActor
{
	GENERATED_BODY()

public:
	AKLightsStage();

	void Build(const FKLightsScene& Scene, const FKLightsStageAssets& Assets);

	/** The ball's visible mirrors turn with the reflections, which spin about Z. */
	void SpinBall(double AngleDeg);

	/** Per view: whether the ceiling is cut away, and where the fog's slices start. */
	void ApplyView(const FKLightsView& View);

	const UPrimitiveComponent* GetBallCore() const;

private:
	UStaticMeshComponent* Shape(const TCHAR* Name, UStaticMesh* Mesh, const FVector& Location, const FVector& Scale,
	                            const FLinearColor& Color, float Roughness, float Metallic, float Emissive);

	FKLightsStageAssets Assets;

	UPROPERTY() TObjectPtr<USceneComponent> Root;
	UPROPERTY() TObjectPtr<UStaticMeshComponent> Ceiling;
	UPROPERTY() TObjectPtr<UStaticMeshComponent> BallCore;
	UPROPERTY() TObjectPtr<USceneComponent> BallPivot;
	UPROPERTY() TObjectPtr<UInstancedStaticMeshComponent> BallTiles;
	UPROPERTY() TObjectPtr<UExponentialHeightFogComponent> Fog;
	UPROPERTY() TObjectPtr<UPostProcessComponent> Look;
	UPROPERTY() TObjectPtr<USkyLightComponent> Sky;
	bool bWalls = true;
};
