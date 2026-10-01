#pragma once

#include "CoreMinimal.h"

/**
 * What the app does with a DMX frame, before anything is drawn.
 *
 * Every function here has a Python twin in engine/scene.py, and THE PYTHON IS
 * THE DEFINITION: engine/tests/data/previz_parity.json records what it says,
 * and the KLights.Parity automation tests hold this file to it at 1e-9. Doubles
 * throughout for that reason -- FVector3f would fail the comparison, not the
 * show.
 *
 * Plain C++, no UObjects, so it is testable without a world.
 */
namespace KLights
{
	/**
	 * One moving head's resolved aim frame, exactly as the manifest ships it.
	 * The engine has already backed the mount facing and elevation zero-error out
	 * of the calibration; this only applies them.
	 */
	struct FDecodeFrame
	{
		bool bBearingIsPan = false;     // "bearing_channel" == "pan"
		bool bElevationIsPan = true;    // "elevation_channel" == "pan"
		double BearingMax = 270.0;
		double ElevationMax = 540.0;
		bool bBearingInvert = false;
		bool bElevationInvert = false;
		bool bElevationCentred = true;  // "elevation_anchor" == "center"
		double ElevationOffset = 0.0;
		double MountFacing = 0.0;
		double Half = 32768.0;
		double Span = 65535.0;
	};

	/** geometry.from_dmx_centered. */
	KLIGHTSPREVIZ_API double FromDmxCentred(double Dmx, double MaxDeg, bool bInvert, double Half);
	/** geometry.from_dmx_zero_elev. */
	KLIGHTSPREVIZ_API double FromDmxZeroElev(double Dmx, double MaxDeg, bool bInvert, double Span);

	/** scene.decode_aim: (world bearing, elevation) in degrees for (pan, tilt) words. */
	KLIGHTSPREVIZ_API void DecodeAim(const FDecodeFrame& Frame, double Pan, double Tilt,
	                                 double& OutBearing, double& OutElevation);

	/**
	 * scene.beam_direction: the unit beam direction in Unreal axes. Under the
	 * manifest's axis mapping yaw IS world bearing and pitch IS elevation, so
	 * this is a rotator's forward vector, computed in double.
	 */
	KLIGHTSPREVIZ_API FVector BeamDirection(double BearingDeg, double ElevationDeg);

	/**
	 * engine.servo.Servo: a yoke following its command at a finite speed, in the
	 * 16-bit words the fixture is addressed in. The first command snaps, so a
	 * previz starting up shows the rig where the console has it.
	 */
	struct KLIGHTSPREVIZ_API FServo
	{
		double PanRate = 0.0;   // words per second
		double TiltRate = 0.0;
		double Pan = 0.0;
		double Tilt = 0.0;
		bool bStarted = false;
		double TargetPan = 0.0;
		double TargetTilt = 0.0;

		/** Advance toward (Pan, Tilt) over Dt seconds; returns where the yoke IS. */
		FVector2D Follow(double InPan, double InTilt, double Dt);
		/** Jump to the last command. For stills. */
		void Settle();
		bool Arrived(double Tolerance = 8.0) const;
	};

	/** A mechanical colour wheel slot: the averaged colour, then the two halves. */
	struct FColorSlot
	{
		int32 Lo = 0;
		int32 Hi = 0;
		FVector Average = FVector::OneVector;   // 0-1 linear
		FVector Top = FVector::OneVector;
		FVector Bottom = FVector::OneVector;
		bool IsSplit() const { return Top != Bottom; }
	};

	/** Role -> 0-based index into the fixture's universe frame; INDEX_NONE if absent. */
	struct FChannels
	{
		int32 Pan = INDEX_NONE, PanFine = INDEX_NONE, Tilt = INDEX_NONE, TiltFine = INDEX_NONE;
		int32 Dimmer = INDEX_NONE, Red = INDEX_NONE, Green = INDEX_NONE, Blue = INDEX_NONE;
		int32 White = INDEX_NONE, ColorWheel = INDEX_NONE, Strobe = INDEX_NONE;
	};

	struct FOutput
	{
		double Level = 0.0;
		FVector Color = FVector::OneVector;
		bool bSplit = false;
		FVector Top = FVector::OneVector;
		FVector Bottom = FVector::OneVector;
	};

	/** scene.channel_word: coarse << 8 | fine, or coarse << 8 with no fine. */
	KLIGHTSPREVIZ_API int32 ChannelWord(const uint8* Frame, int32 Coarse, int32 Fine);

	/** scene.decode_color: (rgb, split) from whichever colour system a fixture has. */
	KLIGHTSPREVIZ_API void DecodeColor(const FChannels& Channels, const TArray<FColorSlot>& Slots,
	                                   const uint8* Frame, FVector& OutColor, bool& bOutSplit,
	                                   FVector& OutTop, FVector& OutBottom);

	/** scene.fixture_output: one rule for every kind of fixture. */
	KLIGHTSPREVIZ_API FOutput FixtureOutput(const FChannels& Channels, const TArray<FColorSlot>& Slots,
	                                        const uint8* Frame);
}
