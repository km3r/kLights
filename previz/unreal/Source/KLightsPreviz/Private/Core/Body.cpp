#include "Core/Body.h"

namespace KLights::Body
{
	namespace
	{
		// Which way FQuat turns, measured rather than assumed: Unreal is
		// left-handed, and getting a sign wrong here points every head backwards.
		double PanSign()
		{
			return FQuat(FVector::ZAxisVector, UE_DOUBLE_HALF_PI).RotateVector(FVector::XAxisVector).Y > 0.0 ? 1.0 : -1.0;
		}

		double TiltSign()
		{
			return FQuat(FVector::XAxisVector, UE_DOUBLE_HALF_PI).RotateVector(FVector::YAxisVector).Z > 0.0 ? 1.0 : -1.0;
		}

		double Wrap(double Radians)
		{
			return FMath::UnwindRadians(Radians);
		}
	}

	FQuat BaseRotation(const FString& MountMode, double MountFacingDeg)
	{
		const double F = FMath::DegreesToRadians(MountFacingDeg);
		const FVector Forward(FMath::Cos(F), FMath::Sin(F), 0.0);
		const FVector Right(-FMath::Sin(F), FMath::Cos(F), 0.0);
		FVector PanAxis = FVector::UpVector;          // "table": base down, pan axis up
		if (MountMode == TEXT("hung"))
		{
			PanAxis = -FVector::UpVector;             // upside down
		}
		else if (MountMode == TEXT("venue"))
		{
			PanAxis = Right;                          // tipped onto its side: pan carries elevation
		}
		return FRotationMatrix::MakeFromYZ(Forward, PanAxis).ToQuat();
	}

	FQuat AimRotation(const FVector& Direction)
	{
		const FVector D = Direction.GetSafeNormal();
		if (FMath::Abs(D.Z) > 0.999)
		{
			return FRotationMatrix::MakeFromYX(D, FVector::XAxisVector).ToQuat();
		}
		return FRotationMatrix::MakeFromYZ(D, FVector::UpVector).ToQuat();
	}

	FVector Compose(double Pan, double Tilt)
	{
		return (FQuat(FVector::ZAxisVector, Pan) * FQuat(FVector::XAxisVector, Tilt)).RotateVector(FVector::YAxisVector);
	}

	void Solve(const FVector& LocalDirection, double PreviousPan, double& OutPan, double& OutTilt)
	{
		// Compose(pan, tilt) = (-sz cos(t) sin(p), cos(t) cos(p), sx sin(t)).
		const FVector L = LocalDirection.GetSafeNormal();
		const double Sz = PanSign();
		const double Sx = TiltSign();
		const double Horizontal = FMath::Sqrt(L.X * L.X + L.Y * L.Y);
		if (Horizontal < 1e-9)
		{
			OutPan = PreviousPan;   // along the pan axis: any pan will do
			OutTilt = Sx * L.Z > 0.0 ? UE_DOUBLE_HALF_PI : -UE_DOUBLE_HALF_PI;
			return;
		}
		const double Pan = FMath::Atan2(-Sz * L.X, L.Y);
		const double Tilt = FMath::Atan2(Sx * L.Z, Horizontal);
		// The other solution: half a turn of pan, with the tilt folded over the top.
		const double PanB = Wrap(Pan + UE_DOUBLE_PI);
		const double TiltB = Wrap((Tilt >= 0.0 ? UE_DOUBLE_PI : -UE_DOUBLE_PI) - Tilt);
		if (FMath::Abs(Wrap(PanB - PreviousPan)) < FMath::Abs(Wrap(Pan - PreviousPan)))
		{
			OutPan = PanB;
			OutTilt = TiltB;
		}
		else
		{
			OutPan = Pan;
			OutTilt = Tilt;
		}
	}
}
