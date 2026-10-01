#pragma once

#include "CoreMinimal.h"

/**
 * Posing a fixture's body so its head points exactly where its beam goes.
 *
 * The beam is ALWAYS drawn from the show's decode, never from the mesh: on the
 * sideways "venue" mount the engine models bearing and elevation as two
 * independent channels, which a physical two-axis gimbal does not reproduce off
 * its home pose. Driving the joints from raw channel angles would therefore
 * swing the head away from its own beam. So the joints are SOLVED instead:
 * whatever pan and tilt put the head along the decoded beam, given how the base
 * is mounted. On an upright or hung base that is just the channel angles.
 *
 * The body convention (docs/models.md), in Unreal axes once the glTF is loaded:
 * the yoke pans about the base's +Z, the head tilts about the yoke's +X, and at
 * rest the beam leaves along +Y (glTF's +Z, the asset's front).
 */
namespace KLights::Body
{
	/** The base's world rotation for a mount mode and a mount facing (degrees, UE yaw = bearing). */
	KLIGHTSPREVIZ_API FQuat BaseRotation(const FString& MountMode, double MountFacingDeg);

	/** A fixed fixture's whole body, front along `Direction`. */
	KLIGHTSPREVIZ_API FQuat AimRotation(const FVector& Direction);

	/** The beam direction, in the base's frame, for a yoke pan and head tilt (radians). */
	KLIGHTSPREVIZ_API FVector Compose(double Pan, double Tilt);

	/**
	 * Pan and tilt (radians) pointing the head along `LocalDirection` (the base's
	 * frame). Of the two solutions, the one whose pan is nearest `PreviousPan` --
	 * so a head never flips half a turn mid-move. Along the pan axis itself any
	 * pan will do, and the previous one is kept.
	 */
	KLIGHTSPREVIZ_API void Solve(const FVector& LocalDirection, double PreviousPan, double& OutPan, double& OutTilt);
}
