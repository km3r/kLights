#pragma once

#include "CoreMinimal.h"
#include "Core/ArtNet.h"
#include "GameFramework/GameModeBase.h"
#include "Runtime/ModelLoader.h"
#include "KLightsSpikeGameMode.generated.h"

/**
 * F20a's throwaway: proves, in a PACKAGED build, the five things the
 * standalone previz rests on. Deleted once they hold.
 *
 *   (a) a GLB fetched over HTTP renders, texture and all     -SpikeGlb=<url>
 *   (b) a line trace hits that mesh's runtime trimesh
 *   (c) an instanced mesh keeps a generated material
 *   (d) Art-Net arrives on 6454
 *   (e) a still to judge Lumen against                       -SpikeShot=<png>
 *
 * Every verdict is logged as "SPIKE <letter> PASS|FAIL ..." so the log can be
 * grepped without opening a window.
 */
UCLASS()
class AKLightsSpikeGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	AKLightsSpikeGameMode();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;

private:
	void Fetch(const FString& Url);
	void Place(const FString& GlbPath);
	void Trace();
	void Instanced();
	void Frame();

	FArtNetReceiver ArtNet;
	FKLightsModel Model;
	FVector Site = FVector::ZeroVector;
	double StartedAt = 0.0;
	double ReportAt = 0.0;
	double QuitAt = 0.0;
	float Seconds = 12.f;
	bool bPlaced = false;
	bool bTraced = false;
	bool bShot = false;
	FString ShotPath;

	UPROPERTY()
	TObjectPtr<AActor> ModelActor;
};
