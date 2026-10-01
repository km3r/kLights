#include "Runtime/KLightsFixture.h"

#include "Components/InstancedStaticMeshComponent.h"
#include "Core/Body.h"
#include "KLightsLog.h"
#include "Components/PointLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"

namespace
{
	const FTransform Parked(FQuat::Identity, FVector::ZeroVector, FVector::ZeroVector);

	FLinearColor Linear(const FVector& C, float Alpha = 1.f)
	{
		return FLinearColor(float(C.X), float(C.Y), float(C.Z), Alpha);
	}

	/** A component whose transform is world-space, whatever it hangs off. */
	void Absolute(USceneComponent* Component)
	{
		Component->SetUsingAbsoluteLocation(true);
		Component->SetUsingAbsoluteRotation(true);
		Component->SetUsingAbsoluteScale(true);
	}

	/** Light, not an object: no shadows, no collision, no decals. */
	void Ghost(UPrimitiveComponent* Component)
	{
		Component->SetCastShadow(false);
		Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Component->SetReceivesDecals(false);
	}
}

AKLightsFixture::AKLightsFixture()
{
	PrimaryActorTick.bCanEverTick = false;
	Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	Root->SetMobility(EComponentMobility::Movable);
	RootComponent = Root;
}

USpotLightComponent* AKLightsFixture::MakeSpot(const TCHAR* Name)
{
	const FKLightsOptics& O = Context.Scene->Optics;
	USpotLightComponent* S = NewObject<USpotLightComponent>(this, Name);
	S->SetupAttachment(Root);
	S->SetMobility(EComponentMobility::Movable);
	S->SetUsingAbsoluteRotation(true);
	S->IntensityUnits = ELightUnits::Lumens;
	S->Intensity = float(Fixture.Lumens);
	// A hard-edged beam: cheap fixed-lens fixtures have no frost.
	const float Half = float(FMath::Max(0.5, Fixture.BeamDeg / 2.0));
	S->OuterConeAngle = Half;
	S->InnerConeAngle = Half * 0.85f;
	S->AttenuationRadius = float(Context.Scene->MaxThrow * 1.2);
	S->SourceRadius = 2.f;
	S->VolumetricScatteringIntensity = float(O.SpotScattering);
	S->CastShadows = true;
	// Off by default and NOT implied by CastShadows: without it the fog sails
	// straight through the ball the mesh stops on.
	S->bCastVolumetricShadow = true;
	// Not a quality dial: below 2 a narrow beam's shadow map cannot hold the ball.
	S->ShadowResolutionScale = float(O.ShadowResolutionScale);
	// Dark until the engine says otherwise -- a previz that starts bright cannot
	// be told apart from one receiving no DMX at all.
	S->SetVisibility(false);
	S->RegisterComponent();
	AddInstanceComponent(S);
	S->SetWorldRotation(Fixture.RestRotation);
	return S;
}

void AKLightsFixture::Setup(const FKLightsFixture& InFixture, const FKLightsRigContext& InContext,
                            const FKLightsShares& InShares)
{
	Fixture = InFixture;
	Context = InContext;
	Shares = InShares;
	Servo = KLights::FServo();
	Servo.PanRate = Fixture.PanRate;
	Servo.TiltRate = Fixture.TiltRate;
	const FKLightsScene& Scene = *Context.Scene;
	const FKLightsOptics& O = Scene.Optics;

	SetActorLocationAndRotation(Fixture.Location, Fixture.RestRotation);

	Spot = MakeSpot(TEXT("Spot"));
	// A split wheel position puts two colours in the aperture, and a spot light
	// has one: so the two halves are two lights, tipped apart. Only built where
	// the wheel has split slots, because a shadow-casting light is not free.
	if (Fixture.HasSplitSlot())
	{
		SpotHalf = MakeSpot(TEXT("SpotHalf"));
	}

	Shaft = NewObject<UStaticMeshComponent>(this, TEXT("Shaft"));
	Shaft->SetupAttachment(Root);
	Absolute(Shaft);
	Shaft->SetMobility(EComponentMobility::Movable);
	Shaft->SetStaticMesh(Context.Cone);
	ShaftMaterial = UMaterialInstanceDynamic::Create(Context.Beam, this);
	Shaft->SetMaterial(0, ShaftMaterial);
	Ghost(Shaft);
	Shaft->SetVisibility(false);
	Shaft->RegisterComponent();
	AddInstanceComponent(Shaft);

	// What the ball sprays back into the room, as actual light. Without it the
	// pose the whole show is built around -- every head on the ball -- lights
	// nothing at all: the reflections are additive meshes and emit nothing.
	Glow = NewObject<UPointLightComponent>(this, TEXT("BallGlow"));
	Glow->SetupAttachment(Root);
	Absolute(Glow);
	Glow->SetMobility(EComponentMobility::Movable);
	Glow->IntensityUnits = ELightUnits::Lumens;
	Glow->Intensity = 0.f;
	Glow->AttenuationRadius = float(Scene.MaxThrow);
	Glow->SourceRadius = float(Context.BallRadius);
	Glow->CastShadows = false;
	Glow->VolumetricScatteringIntensity = float(O.BallGlowScatter);
	Glow->SetVisibility(false);
	Glow->RegisterComponent();
	AddInstanceComponent(Glow);
	Glow->SetWorldLocation(Context.BallCentre);

	// The ball's reflections of this fixture: the shafts leaving it and the dots
	// they land on. Half the facets is a real upper bound -- only facets turned
	// toward the beam can throw one. Instances park at zero scale: an instanced
	// mesh has no per-instance visibility.
	Capacity = Context.FacetCount / 2 + 2;
	auto Pool = [this](const TCHAR* Name, UStaticMesh* Mesh, UMaterialInterface* Material,
	                   TObjectPtr<UMaterialInstanceDynamic>& OutMaterial)
	{
		UInstancedStaticMeshComponent* Ism = NewObject<UInstancedStaticMeshComponent>(this, Name);
		Ism->SetupAttachment(Root);
		Absolute(Ism);
		Ism->SetMobility(EComponentMobility::Movable);
		Ism->SetStaticMesh(Mesh);
		OutMaterial = UMaterialInstanceDynamic::Create(Material, this);
		Ism->SetMaterial(0, OutMaterial);
		Ghost(Ism);
		Ism->RegisterComponent();
		AddInstanceComponent(Ism);
		Ism->SetWorldTransform(FTransform::Identity);
		TArray<FTransform> Empty;
		Empty.Init(Parked, Capacity);
		Ism->AddInstances(Empty, false, /*bWorldSpace=*/ true);
		return Ism;
	};
	Dots = Pool(TEXT("Dots"), Context.Plane, Context.Dot, DotMaterial);
	Rays = Pool(TEXT("Rays"), Context.Cone, Context.Beam, RayMaterial);
	// Every reflected shaft starts at the ball and the ball does not move, so
	// their taper is fixed: one write here, not hundreds a frame.
	RayMaterial->SetVectorParameterValue(TEXT("Origin"), Linear(Context.BallCentre, 0.f));
	RayMaterial->SetScalarParameterValue(TEXT("Reach"), float(Scene.MaxThrow));
	RayMaterial->SetScalarParameterValue(TEXT("Falloff"),
	                                     float(KLights::Look::Surviving(Scene.MaxThrow, O.BeamExtinctionPerM)));

	const FKLightsModel* BodyModel = Context.Model ? Context.Model(Fixture.Body.Model, false) : nullptr;
	if (BodyModel != nullptr)
	{
		BuildModelBody(*BodyModel);
	}
	else
	{
		BuildBoxBody();
	}
}

void AKLightsFixture::BuildBoxBody()
{
	// A stand-in housing. Never collides (a beam's trace starts inside it) and
	// never shadows (its own light is inside it).
	Body = NewObject<UStaticMeshComponent>(this, TEXT("Body"));
	Body->SetupAttachment(Root);
	Body->SetMobility(EComponentMobility::Movable);
	Body->SetStaticMesh(Context.Cube);
	UMaterialInstanceDynamic* BodyMaterial = UMaterialInstanceDynamic::Create(Context.Surface, this);
	BodyMaterial->SetVectorParameterValue(TEXT("BaseColor"), FLinearColor(0.05f, 0.05f, 0.05f));
	BodyMaterial->SetScalarParameterValue(TEXT("Roughness"), 0.4f);
	Body->SetMaterial(0, BodyMaterial);
	Ghost(Body);
	// Cube is 100 cm a side: X along the beam (depth), Y across (width), Z up.
	const FVector Size = Fixture.Body.bHasSize ? Fixture.Body.Size : FVector(18.0, 28.0, 20.0);
	Body->SetRelativeScale3D(FVector(Size.Z, Size.X, Size.Y) / 100.0);
	Body->RegisterComponent();
	AddInstanceComponent(Body);
}

void AKLightsFixture::BuildModelBody(const FKLightsModel& Model)
{
	BodyRoot = NewObject<USceneComponent>(this, TEXT("BodyRoot"));
	BodyRoot->SetupAttachment(Root);
	Absolute(BodyRoot);
	BodyRoot->SetMobility(EComponentMobility::Movable);
	BodyRoot->RegisterComponent();
	AddInstanceComponent(BodyRoot);
	BodyRoot->SetWorldTransform(FTransform::Identity);

	TMap<FString, USceneComponent*> Parts;
	FKLightsModelLoader::Instantiate(Model, this, BodyRoot, false, Parts);
	for (const TPair<FString, USceneComponent*>& Part : Parts)
	{
		if (UPrimitiveComponent* Primitive = Cast<UPrimitiveComponent>(Part.Value))
		{
			Ghost(Primitive);   // same reasons as the box: its light starts inside it
		}
	}
	Yoke = Parts.FindRef(Fixture.Body.YokeNode);
	HeadPart = Parts.FindRef(Fixture.Body.HeadNode);
	USceneComponent* Lens = Parts.FindRef(Fixture.Body.LensNode);

	// How the base is mounted. A mover's base follows its mount mode and the
	// facing its calibration backed out; a fixed fixture's whole body looks
	// along its beam. Either can be overridden from rig.json.
	const FQuat Base = Fixture.Body.bHasRotation
		? Fixture.Body.Rotation.Quaternion()
		: Fixture.bMover
			? KLights::Body::BaseRotation(Context.Scene->MountMode, Fixture.Decode.MountFacing)
			: KLights::Body::AimRotation(KLights::BeamDirection(Fixture.RestRotation.Yaw, Fixture.RestRotation.Pitch));

	// The model's anchor goes where the beam starts: a mover's tilt pivot (which
	// sits on the pan axis, so panning never moves it), a fixed fixture's lens.
	// BodyRoot is still at the world origin, unrotated, so these are model-local.
	USceneComponent* Anchor = Fixture.bMover ? (HeadPart ? HeadPart.Get() : Lens) : Lens;
	const FVector AnchorLocal = Anchor ? Anchor->GetComponentLocation() : FVector::ZeroVector;
	if (Yoke != nullptr && Yoke->GetAttachParent() != nullptr)
	{
		PanFrame = Base * Yoke->GetAttachParent()->GetComponentQuat();
	}
	BodyRoot->SetWorldTransform(FTransform(Base, Fixture.Location - Base.RotateVector(AnchorLocal)));

	if (Fixture.bMover && (Yoke == nullptr || HeadPart == nullptr || HeadPart->GetAttachParent() != Yoke))
	{
		UE_LOG(LogKLights, Warning, TEXT("%s: its model has no '%s' > '%s' pair, so the body will not follow the beam"),
		       *Fixture.Name, *Fixture.Body.YokeNode, *Fixture.Body.HeadNode);
		Yoke = nullptr;
		HeadPart = nullptr;
	}
}

void AKLightsFixture::PoseBody(const FVector& Direction)
{
	if (Yoke == nullptr || HeadPart == nullptr)
	{
		return;
	}
	double Pan, Tilt;
	KLights::Body::Solve(PanFrame.Inverse().RotateVector(Direction), BodyPan, Pan, Tilt);
	BodyPan = Pan;
	Yoke->SetRelativeRotation(FQuat(FVector::ZAxisVector, Pan));
	HeadPart->SetRelativeRotation(FQuat(FVector::XAxisVector, Tilt));
}

void AKLightsFixture::Drive(const uint8* Frame, double Dt, const TArray<FVector>& SpunNormals)
{
	if (Frame == nullptr)
	{
		return;   // never heard this universe: stay dark at the rest pose
	}
	const KLights::FChannels& C = Fixture.Channels;
	FRotator Aim;
	FVector Direction;
	if (Fixture.bMover)
	{
		// Where the console says to be, then where the yoke has GOT to. Decoding
		// the servo's position rather than the command is what makes a move take
		// time; the decode itself is the show's.
		const FVector2D At = Servo.Follow(KLights::ChannelWord(Frame, C.Pan, C.PanFine),
		                                  KLights::ChannelWord(Frame, C.Tilt, C.TiltFine), Dt);
		double Bearing, Elevation;
		KLights::DecodeAim(Fixture.Decode, At.X, At.Y, Bearing, Elevation);
		Aim = FRotator(Elevation, Bearing, 0.0);
		Direction = KLights::BeamDirection(Bearing, Elevation);
		PoseBody(Direction);
	}
	else
	{
		Aim = Fixture.RestRotation;
		Direction = KLights::BeamDirection(Aim.Yaw, Aim.Pitch);
	}

	const KLights::FOutput Out = KLights::FixtureOutput(C, Fixture.ColorSlots, Frame);
	LightUp(Aim, Out);
	const FVector Origin = GetActorLocation();
	AimBeam(Origin, Direction, Out);
	PlaceReflections(Origin, Direction, Out, SpunNormals);
}

void AKLightsFixture::LightUp(const FRotator& Aim, const KLights::FOutput& Out)
{
	const bool bLit = Out.Level > 0.0;
	if (!Out.bSplit || SpotHalf == nullptr)
	{
		Spot->SetVisibility(bLit);
		if (bLit)
		{
			Spot->SetWorldRotation(Aim);
			Spot->SetIntensity(float(Fixture.Lumens * Out.Level));
			Spot->SetLightColor(Linear(Out.Color));
		}
		if (SpotHalf != nullptr)
		{
			SpotHalf->SetVisibility(false);
		}
		return;
	}
	Spot->SetVisibility(bLit);
	SpotHalf->SetVisibility(bLit);
	if (!bLit)
	{
		return;
	}
	// Half the output each, because each half-aperture passes half the beam;
	// tipped a quarter of the cone apart in elevation, overlapping down the
	// middle as the real halves do. The primary keeps the bottom colour.
	const float Half = float(Fixture.Lumens * Out.Level * 0.5);
	Spot->SetIntensity(Half);
	SpotHalf->SetIntensity(Half);
	Spot->SetLightColor(Linear(Out.Bottom));
	SpotHalf->SetLightColor(Linear(Out.Top));
	const double Tilt = Fixture.BeamDeg / 4.0;
	SpotHalf->SetWorldRotation(FRotator(Aim.Pitch + Tilt, Aim.Yaw, Aim.Roll));
	Spot->SetWorldRotation(FRotator(Aim.Pitch - Tilt, Aim.Yaw, Aim.Roll));
}

void AKLightsFixture::AimBeam(const FVector& Origin, const FVector& Direction, const KLights::FOutput& Out)
{
	const FKLightsScene& Scene = *Context.Scene;
	const FKLightsOptics& O = Scene.Optics;
	if (Out.Level <= 0.0)
	{
		Shaft->SetVisibility(false);
		// Out with it too, or a head blacking out on the ball leaves its share
		// of the wash hanging there.
		BallGlow(0.0, Out.Color);
		return;
	}
	Shaft->SetVisibility(true);
	const double HalfAngle = Fixture.BeamDeg / 2.0;
	// How much of the CONE clears the ball. The trace below is one line down the
	// axis and can only say yes or no; the light is a real cone.
	const double Clear = KLights::Look::BallClearance(Origin, Direction, HalfAngle, Context.BallCentre, Context.BallRadius);
	const bool bStoppedByBall = Clear <= 0.02;

	FCollisionQueryParams Params(SCENE_QUERY_STAT(KLightsBeam), false, this);
	if (!bStoppedByBall && Context.BallCore != nullptr)
	{
		Params.AddIgnoredComponent(Context.BallCore);
	}
	FHitResult Hit;
	const FVector Far = Origin + Direction * Scene.MaxThrow;
	const double Length = GetWorld()->LineTraceSingleByChannel(Hit, Origin, Far, ECC_Visibility, Params)
		? FMath::Max(10.0, FVector::Dist(Origin, Hit.ImpactPoint))
		: Scene.MaxThrow;
	const double Radius = Length * FMath::Tan(FMath::DegreesToRadians(HalfAngle));

	// The cone's apex is local +Z, so pointing +Z back up the beam puts it at the lens.
	Shaft->SetWorldLocationAndRotation(Origin + Direction * (Length / 2.0),
	                                   FRotationMatrix::MakeFromZ(-Direction).Rotator());
	Shaft->SetWorldScale3D(FVector(2.0 * Radius / 100.0, 2.0 * Radius / 100.0, Length / 100.0));

	BallGlow(Out.Level * (1.0 - Clear), Out.Color);

	// Full brightness when the ball stops the beam dead: every bit of shaft
	// drawn is then between the lens and the ball. Only a beam drawn PAST it is
	// partial.
	const double ShaftShare = bStoppedByBall ? 1.0 : Clear;
	ShaftMaterial->SetVectorParameterValue(TEXT("Color"), Linear(Out.bSplit ? Out.Bottom : Out.Color));
	ShaftMaterial->SetVectorParameterValue(TEXT("ColorB"), Linear(Out.bSplit ? Out.Top : Out.Color));
	ShaftMaterial->SetVectorParameterValue(TEXT("SplitNormal"), Linear(KLights::Look::SplitPlane(Direction), 0.f));
	ShaftMaterial->SetVectorParameterValue(TEXT("Origin"), Linear(Origin, 0.f));
	ShaftMaterial->SetScalarParameterValue(TEXT("Reach"), float(Length));
	ShaftMaterial->SetScalarParameterValue(TEXT("Falloff"), float(KLights::Look::Surviving(Length, O.BeamExtinctionPerM)));
	ShaftMaterial->SetScalarParameterValue(TEXT("Brightness"),
	                                       float(Out.Level * O.BeamGain * ShaftShare * Shares.BeamShare));
}

void AKLightsFixture::BallGlow(double Caught, const FVector& Color)
{
	const bool bLit = Caught > 0.0;
	Glow->SetVisibility(bLit);
	if (bLit)
	{
		Glow->SetIntensity(float(Fixture.Lumens * Context.Scene->Optics.BallGlowFraction * Caught));
		Glow->SetLightColor(Linear(Color));
	}
}

void AKLightsFixture::PlaceReflections(const FVector& Origin, const FVector& Direction, const KLights::FOutput& Out,
                                       const TArray<FVector>& SpunNormals)
{
	namespace Ball = KLights::Ball;
	const FKLightsScene& Scene = *Context.Scene;
	const FKLightsOptics& O = Scene.Optics;

	Hits.Reset();
	if (Out.Level > 0.0 && Scene.Ball.RadiusMm > 0.0)
	{
		Ball::Dots(Ball::PointToShow(Origin), Ball::DirectionToShow(Direction), Fixture.BeamDeg / 2.0,
		           Context.BallShow, Scene.Ball.RadiusMm, Context.RoomShow, SpunNormals,
		           Scene.Ball.ApertureMm, Shares.Stride, Hits);
	}
	// The budget is enforced at the source by the stride; this only drops the
	// tail for a geometry the estimate did not see coming.
	const int32 Limit = FMath::Min(int32(O.ReflectBudget), Capacity);
	if (Hits.Num() > Limit)
	{
		Hits.SetNum(Limit);
	}
	const int32 Shown = Hits.Num();
	const double Share = Shares.Illuminance;
	// A shaft dimmer than about one 8-bit level cannot change an additive
	// picture, and costs as much to build as one that can.
	const bool bRays = Out.Level * O.RayGain * Share > O.RayFloor;

	DotTransforms.Reset();
	RayTransforms.Reset();
	for (const Ball::FDot& Hit : Hits)
	{
		const FVector Facet = Ball::PointToUnreal(Hit.Facet);
		const FVector Landing = Ball::PointToUnreal(Hit.Point);
		const FVector Normal = Ball::DirectionToUnreal(Hit.Normal);
		const double DotScale = Hit.Spot / 10.0 / 100.0;   // mm -> cm -> plane scale
		// Flat ON the wall, lifted clear of it: a dot drawn as a sphere is half
		// buried, and near a room edge the next wall hides the rest.
		DotTransforms.Emplace(FRotationMatrix::MakeFromZ(Normal).Rotator(), Landing + Normal * O.DotLiftCm,
		                      FVector(DotScale, DotScale, 1.0));
		if (bRays)
		{
			const FVector D = Landing - Facet;
			const double Length = FMath::Max(1.0, D.Size());
			const FVector U = D / Length;
			RayTransforms.Emplace(FRotationMatrix::MakeFromZ(-U).Rotator(), Facet + U * (Length / 2.0),
			                      FVector(DotScale, DotScale, Length / 100.0));
		}
	}
	// Park what was in use last frame and is not now -- and rays separately,
	// because they can drop to nothing while the dots stay.
	for (int32 i = Shown; i < DotsShown; ++i)
	{
		DotTransforms.Add(Parked);
	}
	for (int32 i = RayTransforms.Num(); i < RaysShown; ++i)
	{
		RayTransforms.Add(Parked);
	}
	if (DotTransforms.Num() > 0)
	{
		Dots->BatchUpdateInstancesTransforms(0, DotTransforms, true, true, true);
	}
	if (RayTransforms.Num() > 0)
	{
		Rays->BatchUpdateInstancesTransforms(0, RayTransforms, true, true, true);
	}
	DotsShown = Shown;
	RaysShown = bRays ? Shown : 0;

	// Every reflection of one fixture shares one material, so it is written
	// only when the look changes. The spray of a split beam is drawn in the
	// slot's averaged colour -- the shaft carries the split.
	const double Level = FMath::RoundToDouble(Out.Level * Share * 1e4) / 1e4;
	if (Out.Color != LastLookColor || Level != LastLookLevel)
	{
		LastLookColor = Out.Color;
		LastLookLevel = Level;
		DotMaterial->SetVectorParameterValue(TEXT("Color"), Linear(Out.Color));
		DotMaterial->SetScalarParameterValue(TEXT("Brightness"), float(Out.Level * O.DotGain * Share));
		RayMaterial->SetVectorParameterValue(TEXT("Color"), Linear(Out.Color));
		RayMaterial->SetVectorParameterValue(TEXT("ColorB"), Linear(Out.Color));
		RayMaterial->SetScalarParameterValue(TEXT("Brightness"), float(Out.Level * O.RayGain * Share));
	}
}
