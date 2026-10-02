#include "Runtime/KLightsGameMode.h"

#include "Components/InputComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "Engine/World.h"
#include "GameFramework/SpectatorPawn.h"
#include "Runtime/KLightsSubsystem.h"

AKLightsGameMode::AKLightsGameMode()
{
	DefaultPawnClass = ASpectatorPawn::StaticClass();
	PlayerControllerClass = AKLightsPlayerController::StaticClass();
	HUDClass = AKLightsHUD::StaticClass();
}

void AKLightsPlayerController::SetupInputComponent()
{
	Super::SetupInputComponent();
	const FKey Numbers[] = {EKeys::One, EKeys::Two, EKeys::Three, EKeys::Four, EKeys::Five,
	                        EKeys::Six, EKeys::Seven, EKeys::Eight, EKeys::Nine};
	for (int32 i = 0; i < UE_ARRAY_COUNT(Numbers); ++i)
	{
		FInputKeyBinding& Binding = InputComponent->BindKey(Numbers[i], IE_Pressed, this, &AKLightsPlayerController::ToggleOverlay);
		Binding.KeyDelegate.GetDelegateForManualSet().BindLambda([this, i]() { GoToView(i); });
	}
	InputComponent->BindKey(EKeys::H, IE_Pressed, this, &AKLightsPlayerController::ToggleOverlay);
}

void AKLightsPlayerController::GoToView(int32 Index)
{
	if (UKLightsSubsystem* Previz = GetWorld()->GetSubsystem<UKLightsSubsystem>())
	{
		Previz->GoToView(Index, this);
	}
}

void AKLightsPlayerController::ToggleOverlay()
{
	if (AKLightsHUD* Hud = Cast<AKLightsHUD>(GetHUD()))
	{
		Hud->bShowOverlay = !Hud->bShowOverlay;
	}
}

void AKLightsHUD::DrawHUD()
{
	Super::DrawHUD();
	const UKLightsSubsystem* Previz = GetWorld()->GetSubsystem<UKLightsSubsystem>();
	if (!bShowOverlay || Previz == nullptr || Canvas == nullptr)
	{
		return;
	}
	UFont* Font = GEngine->GetSmallFont();
	const FLinearColor Normal(0.85f, 0.85f, 0.85f);
	const FLinearColor Good(0.45f, 0.9f, 0.5f);
	const FLinearColor Bad(1.f, 0.45f, 0.35f);
	float Y = 12.f;
	auto Line = [&](const FString& Text, const FLinearColor& Color)
	{
		DrawText(Text, Color, 14.f, Y, Font, 1.f);
		Y += 16.f;
	};

	const FKLightsScene* Scene = Previz->GetScene();
	Line(Scene ? FString::Printf(TEXT("kLights previz   %s in %s   scene %s   %.0f fps"), *Scene->Event, *Scene->Venue,
	                             *Scene->Rev, Previz->GetFrameMs() > 0.0 ? 1000.0 / Previz->GetFrameMs() : 0.0)
	           : FString(TEXT("kLights previz   no scene yet")), Normal);

	const FKLightsEngineLink& Link = Previz->GetLink();
	switch (Link.GetState())
	{
	case FKLightsEngineLink::EState::Connected:
		Line(FString::Printf(TEXT("engine %s: connected"), *Link.GetBaseUrl()), Good);
		break;
	case FKLightsEngineLink::EState::Offline:
		Line(FString::Printf(TEXT("offline: %s"), *Link.GetMessage()), Normal);
		break;
	case FKLightsEngineLink::EState::Connecting:
		Line(FString::Printf(TEXT("engine %s: connecting"), *Link.GetBaseUrl()), Normal);
		break;
	default:
		Line(FString::Printf(TEXT("engine %s: %s%s"), *Link.GetBaseUrl(), *Link.GetMessage(),
		                     Scene ? TEXT("  (showing the last scene)") : TEXT("")), Bad);
		break;
	}
	if (Link.GetMissingModels() > 0)
	{
		Line(FString::Printf(TEXT("%d model(s) could not be fetched"), Link.GetMissingModels()), Bad);
	}

	TArray<int32> Universes;
	Previz->GetArtNet().Frames().GetKeys(Universes);
	Universes.Sort();
	if (!Previz->GetArtNet().IsListening())
	{
		Line(TEXT("art-net: not listening"), Bad);
	}
	else if (Previz->GetPacketsPerSecond() <= 0.0)
	{
		Line(TEXT("art-net: nothing arriving -- is the engine sending to this machine (--artnet)?"), Bad);
	}
	else
	{
		Line(FString::Printf(TEXT("art-net: %.0f packets/s, universe(s) %s%s"), Previz->GetPacketsPerSecond(),
		                     *FString::JoinBy(Universes, TEXT(","), [](int32 U) { return FString::FromInt(U); }),
		                     Previz->GetMovingHeads() > 0
		                         ? *FString::Printf(TEXT("   %d head(s) travelling"), Previz->GetMovingHeads())
		                         : TEXT("")), Good);
	}
	if (!Previz->GetProblem().IsEmpty())
	{
		Line(Previz->GetProblem(), Bad);
	}
	for (const FString& ModelProblem : Previz->GetModelProblems())
	{
		Line(ModelProblem, Bad);
	}
	if (Scene)
	{
		if (Scene->Views.IsValidIndex(Previz->GetViewIndex()))
		{
			Line(FString::Printf(TEXT("view %d: %s   (1-%d views, WASD/QE + mouse to fly, H hides this)"),
			                     Previz->GetViewIndex() + 1, *Scene->Views[Previz->GetViewIndex()].Name,
			                     FMath::Min(9, Scene->Views.Num())), Normal);
		}
		for (int32 i = 0; i < FMath::Min(4, Scene->Warnings.Num()); ++i)
		{
			Line(TEXT("! ") + Scene->Warnings[i], Bad);
		}
		if (Scene->Warnings.Num() > 4)
		{
			Line(FString::Printf(TEXT("! ...and %d more warning(s)"), Scene->Warnings.Num() - 4), Bad);
		}
		if (Scene->Unplaced.Num() > 0)
		{
			Line(TEXT("not placed: ") + FString::Join(Scene->Unplaced, TEXT(", ")), Normal);
		}
	}
}
