#pragma once

#include "CoreMinimal.h"
#include "Core/Decode.h"

/**
 * The engine's previz scene, parsed. engine/scene.py writes it; this reads it.
 *
 * STRICT on purpose: every field the app uses must be present, and a missing
 * one fails the whole parse with its name. The alternative -- reading a
 * missing number as 0 -- is how an engine and an app one version apart would
 * render a room with no fog, or a beam with no gain, and nobody would know why.
 */

/** Where to stand to look at the room. Unreal cm. */
struct FKLightsView
{
	FString Name;
	FVector Location = FVector::ZeroVector;
	FVector Target = FVector::ZeroVector;
	double Fov = 70.0;
	bool bHideCeiling = false;
	double FogStart = 0.0;
};

struct FKLightsBody
{
	FString Model;                  // sha256, empty for none
	FString BaseNode = TEXT("base"), YokeNode = TEXT("yoke"), HeadNode = TEXT("head"), LensNode = TEXT("lens");
	bool bHasRotation = false;
	FRotator Rotation = FRotator::ZeroRotator;
	bool bHasSize = false;
	FVector Size = FVector::ZeroVector;   // width, height, depth, cm
};

struct FKLightsFixture
{
	int32 Fid = 0;
	FString Name, Key, Manufacturer, Model, Mode;
	bool bMover = false;
	int32 Universe = 0;
	int32 Address = 1;
	TArray<FString> Tags;
	double BeamDeg = 3.0;
	double Lumens = 0.0;
	KLights::FChannels Channels;
	TArray<KLights::FColorSlot> ColorSlots;
	int32 Head = INDEX_NONE;
	FVector Location = FVector::ZeroVector;
	FRotator RestRotation = FRotator::ZeroRotator;
	KLights::FDecodeFrame Decode;
	double PanRate = 0.0, TiltRate = 0.0;   // words/s
	FKLightsBody Body;

	bool HasSplitSlot() const
	{
		return ColorSlots.ContainsByPredicate([](const KLights::FColorSlot& S) { return S.IsSplit(); });
	}
};

struct FKLightsBar
{
	FString Label;
	FVector Center = FVector::ZeroVector;
	FVector Extent = FVector::ZeroVector;
};

struct FKLightsPlacedModel
{
	FString Name, Model;
	FVector Location = FVector::ZeroVector;
	FRotator Rotation = FRotator::ZeroRotator;
	double Scale = 1.0;
	bool bCollide = true;
};

/** engine/scene.py OPTICS, field for field. */
struct FKLightsOptics
{
	double BeamExtinctionPerM = 0, BeamGain = 0, DotGain = 0, RayGain = 0, RayFloor = 0;
	double MeshContrast = 0, ReflectBudget = 0, DotLiftCm = 0;
	double BallGlowFraction = 0, BallGlowScatter = 0;
	double RoomAlbedo = 0, RoomEmissive = 0;
	double FogDensity = 0, FogHeightFalloff = 0, FogScatteringDistribution = 0;
	double SkyLight = 0, Exposure = 0, BloomIntensity = 0, BloomThreshold = 0;
	double SpotScattering = 0, ShadowResolutionScale = 0, UndeclaredLumens = 0;
};

struct FKLightsBall
{
	FVector Location = FVector::ZeroVector;
	double Radius = 0, RadiusMm = 0, Rpm = 0;
	double MirrorSpacingMm = 0, ReflectSpacingMm = 0, ApertureMm = 0;
	double TileCoverage = 0, TileLiftCm = 0;
	FString Model;
};

struct FKLightsScene
{
	static constexpr int32 SupportedVersion = 1;

	FString Rev, Event, Venue, MountMode;
	TArray<int32> Universes;
	FVector RoomSize = FVector::ZeroVector;   // [X=depth, Y=width, Z=height] cm
	bool bWalls = true;
	FKLightsBall Ball;
	bool bHasCanopy = false;
	FVector CanopyLocation = FVector::ZeroVector;
	double CanopyRadius = 0;
	TArray<FKLightsBar> Truss;
	bool bHasCrowd = false;
	FVector CrowdCenter = FVector::ZeroVector, CrowdExtent = FVector::ZeroVector;
	double MaxThrow = 0;
	FKLightsOptics Optics;
	TArray<FKLightsFixture> Fixtures;
	TArray<FKLightsPlacedModel> Models;
	TMap<FString, FString> AssetNames;   // sha -> file name, for messages
	TArray<FKLightsView> Views;          // overview, corner, audience, ball first
	TArray<FString> Warnings, Unplaced;

	/** The room in the show's frame, mm (width, height, depth) -- what the ball math wants. */
	FVector RoomShowMm() const { return FVector(RoomSize.Y, RoomSize.Z, RoomSize.X) * 10.0; }
};

/** Parse a manifest. On failure, `OutError` names the first missing or malformed field. */
KLIGHTSPREVIZ_API bool ParseKLightsScene(const FString& Json, FKLightsScene& Out, FString& OutError);

class FJsonObject;
class FJsonValue;

/** A fixture's `channels` object (role -> universe index). Exposed for the parity tests. */
KLIGHTSPREVIZ_API KLights::FChannels ParseKLightsChannels(const TSharedPtr<FJsonObject>& Channels);
/** A fixture's `color_slots` list. False if a slot is not 11 numbers. */
KLIGHTSPREVIZ_API bool ParseKLightsColorSlots(const TArray<TSharedPtr<FJsonValue>>& Slots,
                                              TArray<KLights::FColorSlot>& Out);
