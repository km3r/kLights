#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "GameFramework/PlayerController.h"
#include "KLightsGameMode.generated.h"

/**
 * The app's game mode: a free-flying spectator camera, number keys for the
 * scene's own views, and a status overlay. Nothing here knows any event --
 * everything the room contains comes from UKLightsSubsystem.
 */
UCLASS()
class KLIGHTSPREVIZ_API AKLightsGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	AKLightsGameMode();
};

/**
 * WASD / Q E / mouse fly the camera (ASpectatorPawn's own bindings, no input
 * assets); 1-9 jump to the scene's views; H hides the overlay.
 */
UCLASS()
class KLIGHTSPREVIZ_API AKLightsPlayerController : public APlayerController
{
	GENERATED_BODY()

protected:
	virtual void SetupInputComponent() override;

private:
	void GoToView(int32 Index);
	void ToggleOverlay();
};

/** What the previz is showing, where it came from, and whether DMX is arriving. */
UCLASS()
class KLIGHTSPREVIZ_API AKLightsHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void DrawHUD() override;

	bool bShowOverlay = true;
};
