#pragma once

#include "CoreMinimal.h"

/**
 * How the previz LOOKS: the mirror ball's reflections and the beam stand-ins.
 *
 * Ported from previz/mirrorball.py and the editor driver's pure helpers, and
 * checked once against them (previz/unreal/Tests/look_fixture.json, the
 * KLights.Look tests). Unlike Decode.h, this file IS the definition now: the
 * Python it came from is retired, and how the room looks is the renderer's to
 * own.
 *
 * The mirror ball works in the SHOW's frame -- millimetres, y up, origin at the
 * room's front-left floor corner -- because that is the frame it was written,
 * tuned and tested in. ToUnreal converts at the boundary. Everything else here
 * is frame-free or in Unreal centimetres, as noted.
 */
namespace KLights::Ball
{
	/** Show frame (mm, y up) -> Unreal (cm, z up): UE (X, Y, Z) = (z, x, y) / 10. */
	inline FVector PointToUnreal(const FVector& Show) { return FVector(Show.Z, Show.X, Show.Y) / 10.0; }
	/** A direction takes the permutation but not the scale. */
	inline FVector DirectionToUnreal(const FVector& Show) { return FVector(Show.Z, Show.X, Show.Y); }
	inline FVector PointToShow(const FVector& Ue) { return FVector(Ue.Y, Ue.Z, Ue.X) * 10.0; }
	inline FVector DirectionToShow(const FVector& Ue) { return FVector(Ue.Y, Ue.Z, Ue.X); }

	/** (segments, rings) for roughly square facets of `Spacing` mm. */
	KLIGHTSPREVIZ_API FIntPoint Lattice(double Radius, double Spacing);

	/** `Count` normals on a golden-angle spiral: what light reflects off. */
	KLIGHTSPREVIZ_API TArray<FVector> FibonacciNormals(int32 Count);

	struct FTile
	{
		FVector Normal;   // show frame
		FVector East;     // along the ring, the tile's local +X
		double Width = 0.0;
		double Height = 0.0;
	};

	/** The mirrors the ball wears, sized to the patch of sphere each covers. */
	KLIGHTSPREVIZ_API TArray<FTile> FacetTiles(double Radius, int32 Segments, int32 Rings);

	/** Rotate facet normals about the vertical (show y) axis. */
	KLIGHTSPREVIZ_API void Spin(const TArray<FVector>& Normals, double AngleDeg, TArray<FVector>& Out);

	/** Nominal edge of one facet: the ball's area shared out. */
	KLIGHTSPREVIZ_API double FacetSize(double BallRadius, int32 Count);

	/** Where a ray meets the room box's inner surface, and that face's inward normal. */
	KLIGHTSPREVIZ_API bool RayRoomExit(const FVector& Origin, const FVector& Direction, const FVector& Room,
	                                   FVector& OutPoint, FVector& OutNormal);

	/** Cheap rejection: could this cone touch the ball at all? */
	KLIGHTSPREVIZ_API bool BeamHitsBall(const FVector& Origin, const FVector& Direction, double HalfAngleDeg,
	                                    const FVector& Ball, double BallRadius);

	/** Roughly how many facets a beam this wide lights at this range. */
	KLIGHTSPREVIZ_API int32 LitFacets(double Distance, double HalfAngleDeg, double BallRadius, int32 Count);

	struct FDot
	{
		FVector Facet;    // on the ball, where the beamlet leaves (show frame)
		FVector Point;    // where it lands
		FVector Normal;   // inward normal of the surface it lands on
		double Throw = 0.0;
		double Spot = 0.0;   // dot diameter, mm
	};

	/**
	 * Where one beam's reflections land. `Normals` is the already-spun set, shared
	 * by every fixture this frame (there is one ball). `Stride` samples by
	 * LATTICE INDEX, so the same mirrors are drawn every frame and simply enter
	 * and leave the beam as the ball turns -- resampling the hit list instead is
	 * what made the spray crawl.
	 */
	KLIGHTSPREVIZ_API void Dots(const FVector& Origin, const FVector& Direction, double HalfAngleDeg,
	                            const FVector& Ball, double BallRadius, const FVector& Room,
	                            const TArray<FVector>& Normals, double ApertureMm, int32 Stride,
	                            TArray<FDot>& Out);
}

namespace KLights::Look
{
	/**
	 * What fraction of a beam's cone gets PAST the ball, 0-1. Seen from the lens
	 * both are discs; this is one minus their overlap over the cone's area. The
	 * shaft is a line trace, which can only say yes or no, so without this a
	 * beam clipping the ball's edge vanished at the ball while the light it
	 * stands for sailed on and lit the far wall. Any consistent frame and units.
	 */
	KLIGHTSPREVIZ_API double BallClearance(const FVector& Origin, const FVector& Direction, double HalfAngleDeg,
	                                       const FVector& BallCentre, double BallRadius);

	/**
	 * The plane through a beam's axis separating its top half from its bottom,
	 * as an UP-pointing unit normal (Unreal frame). A beam aimed straight up or
	 * down has no top half; +X is picked there, stably, so the split does not
	 * spin as a head swings through vertical.
	 */
	KLIGHTSPREVIZ_API FVector SplitPlane(const FVector& Direction);

	/** Lumens over the cone's solid angle: how bright the beam LOOKS, at full. */
	KLIGHTSPREVIZ_API double Candela(double Lumens, double BeamDeg);

	/** What is left of a beam after `DistanceCm` of haze. */
	KLIGHTSPREVIZ_API double Surviving(double DistanceCm, double ExtinctionPerMetre);
}
