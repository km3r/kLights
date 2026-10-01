#pragma once

#include "CoreMinimal.h"
#include "Core/ArtNet.h"
#include "Runtime/EngineLink.h"
#include "Runtime/Scene.h"
#include "Subsystems/WorldSubsystem.h"
#include "KLightsSubsystem.generated.h"

class AKLightsFixture;
class AKLightsStage;
class APlayerController;
class UMaterialInterface;
class UStaticMesh;

/**
 * The previz, running: the engine's scene in, Art-Net in, the room out.
 *
 * Lives in Game and PIE worlds only -- never the editor's own -- so it can sit
 * beside the editor-Python driver during the transition without the two
 * fighting over UDP 6454.
 *
 * Arguments (command line, else [KLights] in Game.ini):
 *   -Engine=http://host:8765   where the show engine is (default loopback, 8765)
 *   -Scene=<file.json>         render a saved manifest instead; no engine needed
 *   -ArtNetBind=0.0.0.0  -ArtNetPort=6454
 *   -Snapshot=<file.png> [-View=overview] [-SnapshotDelay=3] [-SnapshotHud]
 *                              photograph a view once DMX has arrived, then quit
 */
UCLASS()
class KLIGHTSPREVIZ_API UKLightsSubsystem : public UTickableWorldSubsystem
{
	GENERATED_BODY()

public:
	virtual bool DoesSupportWorldType(const EWorldType::Type WorldType) const override;
	virtual void OnWorldBeginPlay(UWorld& InWorld) override;
	virtual void Deinitialize() override;
	virtual void Tick(float DeltaTime) override;
	virtual TStatId GetStatId() const override;

	const FKLightsScene* GetScene() const { return Scene.Get(); }
	const FKLightsEngineLink& GetLink() const { return Link; }
	const FArtNetReceiver& GetArtNet() const { return ArtNet; }
	double GetPacketsPerSecond() const { return PacketsPerSecond; }
	const FString& GetProblem() const { return Problem; }
	int32 GetViewIndex() const { return View; }
	int32 GetMovingHeads() const;

	/** Stand where view `Index` says, with its field of view, fog and ceiling. */
	void GoToView(int32 Index, APlayerController* Controller);

	/** Finish every head's travel now. For stills. */
	void SettleAll();

private:
	void Rebuild(const FKLightsScene& NewScene);
	void Clear();
	bool LoadAssets();

	FKLightsEngineLink Link;
	FArtNetReceiver ArtNet;
	TUniquePtr<FKLightsScene> Scene;

	UPROPERTY() TObjectPtr<AKLightsStage> Stage;
	UPROPERTY() TArray<TObjectPtr<AKLightsFixture>> Fixtures;
	UPROPERTY() TObjectPtr<UStaticMesh> Cube;
	UPROPERTY() TObjectPtr<UStaticMesh> Sphere;
	UPROPERTY() TObjectPtr<UStaticMesh> Cylinder;
	UPROPERTY() TObjectPtr<UStaticMesh> Cone;
	UPROPERTY() TObjectPtr<UStaticMesh> Plane;
	UPROPERTY() TObjectPtr<UMaterialInterface> SurfaceMaterial;
	UPROPERTY() TObjectPtr<UMaterialInterface> BeamMaterial;
	UPROPERTY() TObjectPtr<UMaterialInterface> DotMaterial;

	/** The ball's reflection facets (show frame), and this frame's spun copy. */
	TArray<FVector> Normals;
	TArray<FVector> Spun;
	double BallAngle = 0.0;

	int32 View = 0;
	bool bCameraPlaced = false;
	bool bStarted = false;
	FString Problem;

	double WindowStart = 0.0;
	uint64 WindowPackets = 0;
	double PacketsPerSecond = 0.0;

	/** -Snapshot: a still of one view, for docs and for comparing builds. */
	void TickSnapshot(double Now);
	FString SnapshotPath;
	FString SnapshotView;
	double SnapshotDelay = 3.0;
	double SnapshotAt = 0.0;
	uint64 SnapshotFrame = 0;
	double QuitAt = 0.0;
};
