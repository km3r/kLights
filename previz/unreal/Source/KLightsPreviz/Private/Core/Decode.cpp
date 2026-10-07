#include "Core/Decode.h"

namespace KLights
{
	static double Radians(double Degrees)
	{
		// math.radians, spelled the same way: x * (pi / 180).
		return Degrees * (UE_DOUBLE_PI / 180.0);
	}

	double FromDmxCentred(double Dmx, double MaxDeg, bool bInvert, double Half)
	{
		const double Delta = (Dmx - Half) / Half * (MaxDeg / 2.0);
		return bInvert ? -Delta : Delta;
	}

	double FromDmxZeroElev(double Dmx, double MaxDeg, bool bInvert, double Span)
	{
		const double Elevation = (Dmx / Span) * MaxDeg - 90.0;
		return bInvert ? -Elevation : Elevation;
	}

	void DecodeAim(const FDecodeFrame& F, double Pan, double Tilt, double& OutBearing, double& OutElevation)
	{
		const double BearingWord = F.bBearingIsPan ? Pan : Tilt;
		const double ElevationWord = F.bElevationIsPan ? Pan : Tilt;
		const double BearingDelta = FromDmxCentred(BearingWord, F.BearingMax, F.bBearingInvert, F.Half);
		const double Elevation = F.bElevationCentred
			? FromDmxCentred(ElevationWord, F.ElevationMax, F.bElevationInvert, F.Half)
			: FromDmxZeroElev(ElevationWord, F.ElevationMax, F.bElevationInvert, F.Span);
		OutBearing = F.MountFacing + BearingDelta;
		OutElevation = Elevation - F.ElevationOffset;
	}

	FVector BeamDirection(double BearingDeg, double ElevationDeg)
	{
		const double P = Radians(ElevationDeg);
		const double Y = Radians(BearingDeg);
		return FVector(FMath::Cos(P) * FMath::Cos(Y), FMath::Cos(P) * FMath::Sin(Y), FMath::Sin(P));
	}

	static double Step(double Current, double Target, double Limit)
	{
		const double Delta = Target - Current;
		if (FMath::Abs(Delta) <= Limit)
		{
			return Target;
		}
		return Current + (Delta > 0.0 ? Limit : -Limit);
	}

	FVector2D FServo::Follow(double InPan, double InTilt, double Dt)
	{
		TargetPan = InPan;
		TargetTilt = InTilt;
		if (!bStarted)
		{
			bStarted = true;
			Pan = InPan;
			Tilt = InTilt;
			return FVector2D(Pan, Tilt);
		}
		if (Dt <= 0.0)
		{
			return FVector2D(Pan, Tilt);
		}
		Pan = Step(Pan, InPan, PanRate * Dt);
		Tilt = Step(Tilt, InTilt, TiltRate * Dt);
		return FVector2D(Pan, Tilt);
	}

	void FServo::Settle()
	{
		if (bStarted)
		{
			Pan = TargetPan;
			Tilt = TargetTilt;
		}
	}

	bool FServo::Arrived(double Tolerance) const
	{
		return bStarted && FMath::Abs(Pan - TargetPan) <= Tolerance && FMath::Abs(Tilt - TargetTilt) <= Tolerance;
	}

	int32 ChannelWord(const uint8* Frame, int32 Coarse, int32 Fine)
	{
		if (Coarse == INDEX_NONE)
		{
			return 0;
		}
		return (int32(Frame[Coarse]) << 8) | (Fine == INDEX_NONE ? 0 : int32(Frame[Fine]));
	}

	void DecodeColor(const FChannels& C, const TArray<FColorSlot>& Slots, const uint8* Frame,
	                 FVector& OutColor, bool& bOutSplit, FVector& OutTop, FVector& OutBottom)
	{
		bOutSplit = false;
		OutTop = OutBottom = FVector::OneVector;
		if (C.Red != INDEX_NONE)
		{
			auto Value = [Frame](int32 Index) { return Index == INDEX_NONE ? 0.0 : Frame[Index] / 255.0; };
			const double W = Value(C.White);
			OutColor = FVector(FMath::Min(1.0, Value(C.Red) + W), FMath::Min(1.0, Value(C.Green) + W),
			                   FMath::Min(1.0, Value(C.Blue) + W));
			return;
		}
		if (C.ColorWheel == INDEX_NONE)
		{
			OutColor = FVector::OneVector;
			return;
		}
		const int32 Raw = Frame[C.ColorWheel];
		for (const FColorSlot& Slot : Slots)
		{
			if (Slot.Lo <= Raw && Raw <= Slot.Hi)
			{
				OutColor = Slot.Average;
				if (Slot.IsSplit())
				{
					bOutSplit = true;
					OutTop = Slot.Top;
					OutBottom = Slot.Bottom;
				}
				return;
			}
		}
		// Above the last slot the wheel is spinning: white is the honest stand-in
		// for "some color, changing".
		OutColor = FVector::OneVector;
	}

	FOutput FixtureOutput(const FChannels& C, const TArray<FColorSlot>& Slots, const uint8* Frame)
	{
		FOutput Out;
		FVector Color;
		DecodeColor(C, Slots, Frame, Color, Out.bSplit, Out.Top, Out.Bottom);
		const double Peak = FMath::Max3(Color.X, Color.Y, Color.Z);
		const double Dimmer = C.Dimmer == INDEX_NONE ? 1.0 : Frame[C.Dimmer] / 255.0;
		if (Peak <= 0.0)
		{
			Out.Level = 0.0;
			Out.Color = FVector::OneVector;
			return Out;
		}
		Out.Level = Dimmer * Peak;
		Out.Color = Color / Peak;
		return Out;
	}
}
