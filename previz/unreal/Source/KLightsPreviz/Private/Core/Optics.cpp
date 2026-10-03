#include "Core/Optics.h"

namespace
{
	double Radians(double Degrees) { return Degrees * (UE_DOUBLE_PI / 180.0); }
}

namespace KLights::Ball
{
	FIntPoint Lattice(double Radius, double Spacing)
	{
		// Python's round() is round-half-to-EVEN, and so is this.
		const int32 Rings = FMath::Max(6, int32(FMath::RoundHalfToEven(UE_DOUBLE_PI * Radius / Spacing)));
		return FIntPoint(2 * Rings, Rings);
	}

	TArray<FVector> FibonacciNormals(int32 Count)
	{
		TArray<FVector> Out;
		const int32 N = FMath::Max(1, Count);
		Out.Reserve(N);
		const double Golden = UE_DOUBLE_PI * (3.0 - FMath::Sqrt(5.0));
		for (int32 i = 0; i < N; ++i)
		{
			const double Y = 1.0 - 2.0 * (i + 0.5) / N;
			const double Ring = FMath::Sqrt(FMath::Max(0.0, 1.0 - Y * Y));
			const double Phi = Golden * i;
			Out.Emplace(Ring * FMath::Cos(Phi), Y, Ring * FMath::Sin(Phi));
		}
		return Out;
	}

	TArray<FTile> FacetTiles(double Radius, int32 Segments, int32 Rings)
	{
		TArray<FTile> Out;
		Out.Reserve(Segments * Rings);
		const double RingHeight = UE_DOUBLE_PI * Radius / Rings;
		for (int32 r = 0; r < Rings; ++r)
		{
			const double Theta = UE_DOUBLE_PI * (r + 0.5) / Rings;
			const double Width = 2.0 * UE_DOUBLE_PI * Radius * FMath::Sin(Theta) / Segments;
			for (int32 s = 0; s < Segments; ++s)
			{
				const double Phi = 2.0 * UE_DOUBLE_PI * (s + 0.5) / Segments;
				FTile& Tile = Out.AddDefaulted_GetRef();
				Tile.Normal = FVector(FMath::Sin(Theta) * FMath::Cos(Phi), FMath::Cos(Theta),
				                      FMath::Sin(Theta) * FMath::Sin(Phi));
				Tile.East = FVector(-FMath::Sin(Phi), 0.0, FMath::Cos(Phi));
				Tile.Width = Width;
				Tile.Height = RingHeight;
			}
		}
		return Out;
	}

	void Spin(const TArray<FVector>& Normals, double AngleDeg, TArray<FVector>& Out)
	{
		const double C = FMath::Cos(Radians(AngleDeg));
		const double S = FMath::Sin(Radians(AngleDeg));
		Out.SetNumUninitialized(Normals.Num());
		for (int32 i = 0; i < Normals.Num(); ++i)
		{
			const FVector& N = Normals[i];
			Out[i] = FVector(N.X * C - N.Z * S, N.Y, N.X * S + N.Z * C);
		}
	}

	double FacetSize(double BallRadius, int32 Count)
	{
		return FMath::Sqrt(4.0 * UE_DOUBLE_PI * BallRadius * BallRadius / FMath::Max(1, Count));
	}

	bool RayRoomExit(const FVector& Origin, const FVector& Direction, const FVector& Room,
	                 FVector& OutPoint, FVector& OutNormal)
	{
		double Best = TNumericLimits<double>::Max();
		bool bFound = false;
		for (int32 Axis = 0; Axis < 3; ++Axis)
		{
			const double D = Direction[Axis];
			if (FMath::Abs(D) < 1e-12)
			{
				continue;
			}
			// The far plane in this axis' direction of travel.
			const double T = ((D > 0.0 ? Room[Axis] : 0.0) - Origin[Axis]) / D;
			if (0.0 < T && T < Best)
			{
				Best = T;
				bFound = true;
				OutNormal = FVector::ZeroVector;
				OutNormal[Axis] = D > 0.0 ? -1.0 : 1.0;   // inward opposes travel
			}
		}
		if (!bFound)
		{
			return false;
		}
		OutPoint = Origin + Direction * Best;
		return true;
	}

	bool BeamHitsBall(const FVector& Origin, const FVector& Direction, double HalfAngleDeg,
	                  const FVector& Ball, double BallRadius)
	{
		const FVector V = Ball - Origin;
		const double Along = FVector::DotProduct(V, Direction);
		if (Along <= 0.0)
		{
			return false;
		}
		const FVector Perp = V - Along * Direction;
		return Perp.Size() <= BallRadius + Along * FMath::Tan(Radians(HalfAngleDeg));
	}

	int32 LitFacets(double Distance, double HalfAngleDeg, double BallRadius, int32 Count)
	{
		const double R = Distance * FMath::Tan(Radians(FMath::Max(0.01, HalfAngleDeg)));
		if (R >= BallRadius)
		{
			return Count / 2;
		}
		const double Cap = BallRadius - FMath::Sqrt(FMath::Max(0.0, BallRadius * BallRadius - R * R));
		return FMath::Max(1, int32(Count * Cap / (2.0 * BallRadius)));
	}

	void Dots(const FVector& Origin, const FVector& Direction, double HalfAngleDeg,
	          const FVector& Ball, double BallRadius, const FVector& Room,
	          const TArray<FVector>& Normals, double ApertureMm, int32 Stride, TArray<FDot>& Out)
	{
		Out.Reset();
		if (!BeamHitsBall(Origin, Direction, HalfAngleDeg, Ball, BallRadius))
		{
			return;
		}
		const int32 Step = FMath::Max(1, Stride);
		const double TanHalf = FMath::Tan(Radians(HalfAngleDeg));
		// Each drawn facet stands in for `Stride` of them, so it is that much
		// bigger: the same total light over fewer mirrors.
		const double Tile = FacetSize(BallRadius, Normals.Num()) * FMath::Sqrt(double(Step));
		for (int32 i = 0; i < Normals.Num(); i += Step)
		{
			const FVector& N = Normals[i];
			const double Facing = FVector::DotProduct(Direction, N);
			if (Facing >= 0.0)
			{
				continue;
			}
			const FVector Point = Ball + BallRadius * N;
			const FVector W = Point - Origin;
			const double Along = FVector::DotProduct(W, Direction);
			if (Along <= 0.0)
			{
				continue;
			}
			if ((W - Along * Direction).Size() > Along * TanHalf)
			{
				continue;   // outside the beam cone
			}
			const FVector Raw = Direction - 2.0 * Facing * N;
			const double Length = Raw.Size();
			if (Length < 1e-12)
			{
				continue;
			}
			FVector Landing, Surface;
			if (!RayRoomExit(Point, Raw / Length, Room, Landing, Surface))
			{
				continue;
			}
			FDot& Dot = Out.AddDefaulted_GetRef();
			Dot.Facet = Point;
			Dot.Point = Landing;
			Dot.Normal = Surface;
			Dot.Throw = FVector::Dist(Point, Landing);
			// The lens's angular size seen from the facet is the divergence of the
			// bundle the facet throws -- not the fixture's beam angle.
			Dot.Spot = Tile + Dot.Throw * (ApertureMm / FMath::Max(Along, 1.0));
		}
	}
}

namespace KLights::Look
{
	double BallClearance(const FVector& Origin, const FVector& Direction, double HalfAngleDeg,
	                     const FVector& BallCentre, double BallRadius)
	{
		const FVector V = BallCentre - Origin;
		const double Distance = V.Size();
		if (Distance <= BallRadius)
		{
			return 0.0;   // the lens is inside it
		}
		const double Along = FVector::DotProduct(V, Direction) / Distance;
		const double Theta = FMath::Acos(FMath::Clamp(Along, -1.0, 1.0));
		const double Rho = FMath::Asin(BallRadius / Distance);
		const double Alpha = Radians(HalfAngleDeg);

		if (Theta >= Rho + Alpha)
		{
			return 1.0;   // misses the ball entirely
		}
		if (Theta + Alpha <= Rho)
		{
			return 0.0;   // wholly behind it
		}
		if (Theta + Rho <= Alpha)
		{
			return 1.0 - (Rho * Rho) / (Alpha * Alpha);   // the ball sits inside the cone
		}
		// Lens-shaped intersection of two circles.
		double A = (Theta * Theta + Alpha * Alpha - Rho * Rho) / (2.0 * Theta * Alpha);
		double B = (Theta * Theta + Rho * Rho - Alpha * Alpha) / (2.0 * Theta * Rho);
		A = FMath::Acos(FMath::Clamp(A, -1.0, 1.0));
		B = FMath::Acos(FMath::Clamp(B, -1.0, 1.0));
		const double Overlap = Alpha * Alpha * (A - FMath::Sin(2.0 * A) / 2.0)
			+ Rho * Rho * (B - FMath::Sin(2.0 * B) / 2.0);
		return FMath::Max(0.0, 1.0 - Overlap / (UE_DOUBLE_PI * Alpha * Alpha));
	}

	FVector SplitPlane(const FVector& D)
	{
		// World up projected off the beam axis: up - d (d . up), with d . up = d.Z.
		const FVector Up(-D.X * D.Z, -D.Y * D.Z, 1.0 - D.Z * D.Z);
		const double Length = Up.Size();
		if (Length < 1e-6)
		{
			return FVector(1.0, 0.0, 0.0);
		}
		return Up / Length;
	}

	double Candela(double Lumens, double BeamDeg)
	{
		const double Half = Radians(FMath::Max(0.25, BeamDeg) / 2.0);
		return Lumens / (2.0 * UE_DOUBLE_PI * (1.0 - FMath::Cos(Half)));
	}

	double Surviving(double DistanceCm, double ExtinctionPerMetre)
	{
		return FMath::Exp(-ExtinctionPerMetre * DistanceCm / 100.0);
	}
}
