#include "Runtime/Scene.h"

#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	/** Reads fields and remembers the FIRST thing that was missing or wrong. */
	struct FReader
	{
		FString Error;

		bool Ok() const { return Error.IsEmpty(); }

		void Fail(const FString& Where, const TCHAR* Wanted)
		{
			if (Error.IsEmpty())
			{
				Error = FString::Printf(TEXT("%s is missing or is not %s"), *Where, Wanted);
			}
		}

		TSharedPtr<FJsonValue> Field(const TSharedPtr<FJsonObject>& Obj, const FString& Key) const
		{
			return Obj.IsValid() ? Obj->TryGetField(Key) : nullptr;
		}

		double Num(const TSharedPtr<FJsonObject>& Obj, const FString& Key, const FString& Where)
		{
			const TSharedPtr<FJsonValue> V = Field(Obj, Key);
			if (!V.IsValid() || V->Type != EJson::Number)
			{
				Fail(Where + TEXT(".") + Key, TEXT("a number"));
				return 0.0;
			}
			return V->AsNumber();
		}

		bool Bool(const TSharedPtr<FJsonObject>& Obj, const FString& Key, const FString& Where)
		{
			const TSharedPtr<FJsonValue> V = Field(Obj, Key);
			if (!V.IsValid() || V->Type != EJson::Boolean)
			{
				Fail(Where + TEXT(".") + Key, TEXT("true or false"));
				return false;
			}
			return V->AsBool();
		}

		FString Str(const TSharedPtr<FJsonObject>& Obj, const FString& Key, const FString& Where)
		{
			const TSharedPtr<FJsonValue> V = Field(Obj, Key);
			if (!V.IsValid() || V->Type != EJson::String)
			{
				Fail(Where + TEXT(".") + Key, TEXT("text"));
				return FString();
			}
			return V->AsString();
		}

		/** Text, or empty for a JSON null. */
		FString StrOrNull(const TSharedPtr<FJsonObject>& Obj, const FString& Key, const FString& Where)
		{
			const TSharedPtr<FJsonValue> V = Field(Obj, Key);
			if (V.IsValid() && V->Type == EJson::Null)
			{
				return FString();
			}
			return Str(Obj, Key, Where);
		}

		TSharedPtr<FJsonObject> Obj(const TSharedPtr<FJsonObject>& Parent, const FString& Key, const FString& Where)
		{
			const TSharedPtr<FJsonValue> V = Field(Parent, Key);
			if (!V.IsValid() || V->Type != EJson::Object)
			{
				Fail(Where + TEXT(".") + Key, TEXT("an object"));
				return nullptr;
			}
			return V->AsObject();
		}

		/** An object, or null for a JSON null (an optional block). */
		TSharedPtr<FJsonObject> ObjOrNull(const TSharedPtr<FJsonObject>& Parent, const FString& Key, const FString& Where)
		{
			const TSharedPtr<FJsonValue> V = Field(Parent, Key);
			if (V.IsValid() && V->Type == EJson::Null)
			{
				return nullptr;
			}
			return Obj(Parent, Key, Where);
		}

		const TArray<TSharedPtr<FJsonValue>>* Arr(const TSharedPtr<FJsonObject>& Obj, const FString& Key, const FString& Where)
		{
			const TSharedPtr<FJsonValue> V = Field(Obj, Key);
			if (!V.IsValid() || V->Type != EJson::Array)
			{
				Fail(Where + TEXT(".") + Key, TEXT("a list"));
				return nullptr;
			}
			return &V->AsArray();
		}

		FVector Vec(const TSharedPtr<FJsonObject>& Obj, const FString& Key, const FString& Where)
		{
			const TArray<TSharedPtr<FJsonValue>>* A = Arr(Obj, Key, Where);
			if (A == nullptr || A->Num() != 3)
			{
				Fail(Where + TEXT(".") + Key, TEXT("three numbers"));
				return FVector::ZeroVector;
			}
			return FVector((*A)[0]->AsNumber(), (*A)[1]->AsNumber(), (*A)[2]->AsNumber());
		}

		/** The manifest's [pitch, yaw, roll]. */
		FRotator Rot(const TSharedPtr<FJsonObject>& Obj, const FString& Key, const FString& Where)
		{
			const FVector V = Vec(Obj, Key, Where);
			return FRotator(V.X, V.Y, V.Z);
		}
	};

	void ParseFixture(FReader& R, const TSharedPtr<FJsonObject>& J, int32 Index, FKLightsFixture& F)
	{
		const FString Where = FString::Printf(TEXT("fixtures[%d]"), Index);
		F.Fid = int32(R.Num(J, TEXT("fid"), Where));
		F.Name = R.Str(J, TEXT("name"), Where);
		F.Key = R.Str(J, TEXT("key"), Where);
		F.Manufacturer = R.Str(J, TEXT("manufacturer"), Where);
		F.Model = R.Str(J, TEXT("model"), Where);
		F.Mode = R.Str(J, TEXT("mode"), Where);
		const FString Kind = R.Str(J, TEXT("kind"), Where);
		F.bMover = Kind == TEXT("mover");
		if (R.Ok() && !F.bMover && Kind != TEXT("static"))
		{
			R.Fail(Where + TEXT(".kind"), TEXT("'mover' or 'static'"));
		}
		F.Universe = int32(R.Num(J, TEXT("universe"), Where));
		F.Address = int32(R.Num(J, TEXT("address"), Where));
		if (const TArray<TSharedPtr<FJsonValue>>* Tags = R.Arr(J, TEXT("tags"), Where))
		{
			for (const TSharedPtr<FJsonValue>& Tag : *Tags)
			{
				F.Tags.Add(Tag->AsString());
			}
		}
		F.BeamDeg = R.Num(J, TEXT("beam_deg"), Where);
		F.Lumens = R.Num(J, TEXT("lumens"), Where);
		F.Location = R.Vec(J, TEXT("location"), Where);
		F.RestRotation = R.Rot(J, TEXT("rest_rotation"), Where);

		F.Channels = ParseKLightsChannels(R.Obj(J, TEXT("channels"), Where));
		const KLights::FChannels& C = F.Channels;
		for (int32 Slot : {C.Pan, C.PanFine, C.Tilt, C.TiltFine, C.Dimmer, C.Red, C.Green, C.Blue,
		                   C.White, C.ColorWheel, C.Strobe})
		{
			if (Slot != INDEX_NONE && (Slot < 0 || Slot > 511))
			{
				R.Fail(Where + TEXT(".channels"), TEXT("indices within one 512-channel universe"));
			}
		}
		if (const TArray<TSharedPtr<FJsonValue>>* Slots = R.Arr(J, TEXT("color_slots"), Where))
		{
			if (!ParseKLightsColorSlots(*Slots, F.ColorSlots))
			{
				R.Fail(Where + TEXT(".color_slots"), TEXT("[lo, hi, r, g, b, r1, g1, b1, r2, g2, b2] each"));
			}
		}

		if (F.bMover)
		{
			F.Head = int32(R.Num(J, TEXT("head"), Where));
			const TSharedPtr<FJsonObject> D = R.Obj(J, TEXT("decode"), Where);
			const FString DW = Where + TEXT(".decode");
			KLights::FDecodeFrame& Frame = F.Decode;
			Frame.bBearingIsPan = R.Str(D, TEXT("bearing_channel"), DW) == TEXT("pan");
			Frame.bElevationIsPan = R.Str(D, TEXT("elevation_channel"), DW) == TEXT("pan");
			Frame.BearingMax = R.Num(D, TEXT("bearing_max"), DW);
			Frame.ElevationMax = R.Num(D, TEXT("elevation_max"), DW);
			Frame.bBearingInvert = R.Bool(D, TEXT("bearing_invert"), DW);
			Frame.bElevationInvert = R.Bool(D, TEXT("elevation_invert"), DW);
			const FString Anchor = R.Str(D, TEXT("elevation_anchor"), DW);
			Frame.bElevationCentred = Anchor == TEXT("center");
			if (R.Ok() && !Frame.bElevationCentred && Anchor != TEXT("zero"))
			{
				R.Fail(DW + TEXT(".elevation_anchor"), TEXT("'center' or 'zero'"));
			}
			Frame.ElevationOffset = R.Num(D, TEXT("elevation_offset"), DW);
			Frame.MountFacing = R.Num(D, TEXT("mount_facing"), DW);
			Frame.Half = R.Num(D, TEXT("half"), DW);
			Frame.Span = R.Num(D, TEXT("span"), DW);
			const TSharedPtr<FJsonObject> S = R.Obj(J, TEXT("servo"), Where);
			F.PanRate = R.Num(S, TEXT("pan_words_per_s"), Where + TEXT(".servo"));
			F.TiltRate = R.Num(S, TEXT("tilt_words_per_s"), Where + TEXT(".servo"));
		}

		const TSharedPtr<FJsonObject> B = R.Obj(J, TEXT("body"), Where);
		const FString BW = Where + TEXT(".body");
		F.Body.Model = R.StrOrNull(B, TEXT("model"), BW);
		if (const TSharedPtr<FJsonObject> Nodes = R.Obj(B, TEXT("nodes"), BW))
		{
			F.Body.BaseNode = R.Str(Nodes, TEXT("base"), BW + TEXT(".nodes"));
			F.Body.YokeNode = R.Str(Nodes, TEXT("yoke"), BW + TEXT(".nodes"));
			F.Body.HeadNode = R.Str(Nodes, TEXT("head"), BW + TEXT(".nodes"));
			F.Body.LensNode = R.Str(Nodes, TEXT("lens"), BW + TEXT(".nodes"));
		}
		if (B.IsValid() && B->HasTypedField<EJson::Array>(TEXT("rotation")))
		{
			F.Body.bHasRotation = true;
			F.Body.Rotation = R.Rot(B, TEXT("rotation"), BW);
		}
		if (B.IsValid() && B->HasTypedField<EJson::Array>(TEXT("size")))
		{
			F.Body.bHasSize = true;
			F.Body.Size = R.Vec(B, TEXT("size"), BW);
		}
	}
}

KLights::FChannels ParseKLightsChannels(const TSharedPtr<FJsonObject>& Channels)
{
	auto Index = [&Channels](const TCHAR* Role)
	{
		double Value = 0.0;
		return Channels.IsValid() && Channels->TryGetNumberField(Role, Value) ? int32(Value) : INDEX_NONE;
	};
	KLights::FChannels C;
	C.Pan = Index(TEXT("pan"));
	C.PanFine = Index(TEXT("pan_fine"));
	C.Tilt = Index(TEXT("tilt"));
	C.TiltFine = Index(TEXT("tilt_fine"));
	C.Dimmer = Index(TEXT("dimmer"));
	C.Red = Index(TEXT("red"));
	C.Green = Index(TEXT("green"));
	C.Blue = Index(TEXT("blue"));
	C.White = Index(TEXT("white"));
	C.ColorWheel = Index(TEXT("color_wheel"));
	C.Strobe = Index(TEXT("strobe"));
	return C;
}

bool ParseKLightsColorSlots(const TArray<TSharedPtr<FJsonValue>>& Slots, TArray<KLights::FColorSlot>& Out)
{
	Out.Reset();
	for (const TSharedPtr<FJsonValue>& S : Slots)
	{
		const TArray<TSharedPtr<FJsonValue>>& V = S->AsArray();
		if (V.Num() != 11)
		{
			return false;
		}
		KLights::FColorSlot& Slot = Out.AddDefaulted_GetRef();
		Slot.Lo = int32(V[0]->AsNumber());
		Slot.Hi = int32(V[1]->AsNumber());
		auto Rgb = [&V](int32 At)
		{
			return FVector(V[At]->AsNumber(), V[At + 1]->AsNumber(), V[At + 2]->AsNumber()) / 255.0;
		};
		Slot.Average = Rgb(2);
		Slot.Top = Rgb(5);
		Slot.Bottom = Rgb(8);
	}
	return true;
}

bool ParseKLightsScene(const FString& Json, FKLightsScene& Out, FString& OutError)
{
	Out = FKLightsScene();
	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
	{
		OutError = TEXT("the scene is not valid JSON");
		return false;
	}

	FReader R;
	const FString Format = R.Str(Root, TEXT("format"), TEXT("scene"));
	const int32 Version = int32(R.Num(Root, TEXT("version"), TEXT("scene")));
	if (R.Ok() && Format != TEXT("klights-previz-scene"))
	{
		OutError = FString::Printf(TEXT("this is not a kLights previz scene (format '%s')"), *Format);
		return false;
	}
	if (R.Ok() && Version != FKLightsScene::SupportedVersion)
	{
		OutError = FString::Printf(TEXT("scene version %d, but this app reads version %d -- "
		                                "update whichever of the engine and the app is older"),
		                           Version, FKLightsScene::SupportedVersion);
		return false;
	}

	Out.Rev = R.Str(Root, TEXT("rev"), TEXT("scene"));
	Out.Event = R.Str(Root, TEXT("event"), TEXT("scene"));
	Out.Venue = R.Str(Root, TEXT("venue"), TEXT("scene"));
	Out.MountMode = R.Str(Root, TEXT("mount_mode"), TEXT("scene"));
	if (const TArray<TSharedPtr<FJsonValue>>* U = R.Arr(Root, TEXT("universes"), TEXT("scene")))
	{
		for (const TSharedPtr<FJsonValue>& V : *U)
		{
			Out.Universes.Add(int32(V->AsNumber()));
		}
	}

	const TSharedPtr<FJsonObject> Room = R.Obj(Root, TEXT("room"), TEXT("scene"));
	Out.RoomSize = R.Vec(Room, TEXT("size"), TEXT("room"));
	Out.bWalls = R.Bool(Room, TEXT("walls"), TEXT("room"));

	const TSharedPtr<FJsonObject> Ball = R.Obj(Root, TEXT("ball"), TEXT("scene"));
	Out.Ball.Location = R.Vec(Ball, TEXT("location"), TEXT("ball"));
	Out.Ball.Radius = R.Num(Ball, TEXT("radius"), TEXT("ball"));
	Out.Ball.RadiusMm = R.Num(Ball, TEXT("radius_mm"), TEXT("ball"));
	Out.Ball.Rpm = R.Num(Ball, TEXT("rpm"), TEXT("ball"));
	Out.Ball.MirrorSpacingMm = R.Num(Ball, TEXT("mirror_spacing_mm"), TEXT("ball"));
	Out.Ball.ReflectSpacingMm = R.Num(Ball, TEXT("reflect_spacing_mm"), TEXT("ball"));
	Out.Ball.ApertureMm = R.Num(Ball, TEXT("aperture_mm"), TEXT("ball"));
	Out.Ball.TileCoverage = R.Num(Ball, TEXT("tile_coverage"), TEXT("ball"));
	Out.Ball.TileLiftCm = R.Num(Ball, TEXT("tile_lift_cm"), TEXT("ball"));
	Out.Ball.Model = R.StrOrNull(Ball, TEXT("model"), TEXT("ball"));

	if (const TSharedPtr<FJsonObject> Canopy = R.ObjOrNull(Root, TEXT("canopy"), TEXT("scene")))
	{
		Out.bHasCanopy = true;
		Out.CanopyLocation = R.Vec(Canopy, TEXT("location"), TEXT("canopy"));
		Out.CanopyRadius = R.Num(Canopy, TEXT("radius"), TEXT("canopy"));
	}
	if (const TSharedPtr<FJsonObject> Truss = R.ObjOrNull(Root, TEXT("truss"), TEXT("scene")))
	{
		if (const TArray<TSharedPtr<FJsonValue>>* Bars = R.Arr(Truss, TEXT("bars"), TEXT("truss")))
		{
			for (const TSharedPtr<FJsonValue>& V : *Bars)
			{
				const TSharedPtr<FJsonObject> Bar = V->AsObject();
				FKLightsBar& Made = Out.Truss.AddDefaulted_GetRef();
				Made.Label = R.Str(Bar, TEXT("label"), TEXT("truss.bars"));
				Made.Center = R.Vec(Bar, TEXT("center"), TEXT("truss.bars"));
				Made.Extent = R.Vec(Bar, TEXT("extent"), TEXT("truss.bars"));
			}
		}
	}
	if (const TSharedPtr<FJsonObject> Crowd = R.ObjOrNull(Root, TEXT("crowd_zone"), TEXT("scene")))
	{
		Out.bHasCrowd = true;
		Out.CrowdCenter = R.Vec(Crowd, TEXT("center"), TEXT("crowd_zone"));
		Out.CrowdExtent = R.Vec(Crowd, TEXT("extent"), TEXT("crowd_zone"));
	}
	Out.MaxThrow = R.Num(Root, TEXT("max_throw"), TEXT("scene"));

	const TSharedPtr<FJsonObject> O = R.Obj(Root, TEXT("optics"), TEXT("scene"));
	FKLightsOptics& P = Out.Optics;
	const FString OW = TEXT("optics");
	P.BeamExtinctionPerM = R.Num(O, TEXT("beam_extinction_per_m"), OW);
	P.BeamGain = R.Num(O, TEXT("beam_gain"), OW);
	P.DotGain = R.Num(O, TEXT("dot_gain"), OW);
	P.RayGain = R.Num(O, TEXT("ray_gain"), OW);
	P.RayFloor = R.Num(O, TEXT("ray_floor"), OW);
	P.MeshContrast = R.Num(O, TEXT("mesh_contrast"), OW);
	P.ReflectBudget = R.Num(O, TEXT("reflect_budget"), OW);
	P.DotLiftCm = R.Num(O, TEXT("dot_lift_cm"), OW);
	P.BallGlowFraction = R.Num(O, TEXT("ball_glow_fraction"), OW);
	P.BallGlowScatter = R.Num(O, TEXT("ball_glow_scatter"), OW);
	P.RoomAlbedo = R.Num(O, TEXT("room_albedo"), OW);
	P.RoomEmissive = R.Num(O, TEXT("room_emissive"), OW);
	P.FogDensity = R.Num(O, TEXT("fog_density"), OW);
	P.FogHeightFalloff = R.Num(O, TEXT("fog_height_falloff"), OW);
	P.FogScatteringDistribution = R.Num(O, TEXT("fog_scattering_distribution"), OW);
	P.SkyLight = R.Num(O, TEXT("sky_light"), OW);
	P.Exposure = R.Num(O, TEXT("exposure"), OW);
	P.BloomIntensity = R.Num(O, TEXT("bloom_intensity"), OW);
	P.BloomThreshold = R.Num(O, TEXT("bloom_threshold"), OW);
	P.SpotScattering = R.Num(O, TEXT("spot_scattering"), OW);
	P.ShadowResolutionScale = R.Num(O, TEXT("shadow_resolution_scale"), OW);
	P.UndeclaredLumens = R.Num(O, TEXT("undeclared_lumens"), OW);

	if (const TArray<TSharedPtr<FJsonValue>>* Fixtures = R.Arr(Root, TEXT("fixtures"), TEXT("scene")))
	{
		for (int32 i = 0; i < Fixtures->Num() && R.Ok(); ++i)
		{
			ParseFixture(R, (*Fixtures)[i]->AsObject(), i, Out.Fixtures.AddDefaulted_GetRef());
		}
	}

	if (const TArray<TSharedPtr<FJsonValue>>* Models = R.Arr(Root, TEXT("models"), TEXT("scene")))
	{
		for (int32 i = 0; i < Models->Num(); ++i)
		{
			const TSharedPtr<FJsonObject> M = (*Models)[i]->AsObject();
			const FString Where = FString::Printf(TEXT("models[%d]"), i);
			FKLightsPlacedModel& Made = Out.Models.AddDefaulted_GetRef();
			Made.Name = R.Str(M, TEXT("name"), Where);
			Made.Model = R.Str(M, TEXT("model"), Where);
			Made.Location = R.Vec(M, TEXT("location"), Where);
			Made.Rotation = R.Rot(M, TEXT("rotation"), Where);
			Made.Scale = R.Num(M, TEXT("scale"), Where);
			Made.bCollide = R.Bool(M, TEXT("collide"), Where);
		}
	}

	if (const TSharedPtr<FJsonObject> Assets = R.Obj(Root, TEXT("assets"), TEXT("scene")))
	{
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Assets->Values)
		{
			const TSharedPtr<FJsonObject> A = Pair.Value->AsObject();
			Out.AssetNames.Add(Pair.Key, A.IsValid() ? A->GetStringField(TEXT("name")) : Pair.Key);
		}
	}

	if (const TSharedPtr<FJsonObject> Views = R.Obj(Root, TEXT("views"), TEXT("scene")))
	{
		TArray<FString> Names;
		for (const auto& Pair : Views->Values)
		{
			Names.Add(FString(Pair.Key));
		}
		// The working views first, in the order the number keys reach them.
		const TArray<FString> Preferred = {TEXT("overview"), TEXT("corner"), TEXT("audience"), TEXT("ball")};
		Names.Sort([&Preferred](const FString& A, const FString& B)
		{
			const int32 IA = Preferred.Contains(A) ? Preferred.IndexOfByKey(A) : 100;
			const int32 IB = Preferred.Contains(B) ? Preferred.IndexOfByKey(B) : 100;
			return IA != IB ? IA < IB : A < B;
		});
		for (const FString& Name : Names)
		{
			const TSharedPtr<FJsonObject> V = Views->GetObjectField(Name);
			const FString Where = TEXT("views.") + Name;
			FKLightsView& Made = Out.Views.AddDefaulted_GetRef();
			Made.Name = Name;
			Made.Location = R.Vec(V, TEXT("location"), Where);
			Made.Target = R.Vec(V, TEXT("target"), Where);
			Made.Fov = R.Num(V, TEXT("fov"), Where);
			Made.bHideCeiling = R.Bool(V, TEXT("hide_ceiling"), Where);
			Made.FogStart = R.Num(V, TEXT("fog_start"), Where);
		}
	}

	for (const TCHAR* Key : {TEXT("warnings"), TEXT("unplaced")})
	{
		if (const TArray<TSharedPtr<FJsonValue>>* Lines = R.Arr(Root, Key, TEXT("scene")))
		{
			TArray<FString>& Into = FCString::Strcmp(Key, TEXT("warnings")) == 0 ? Out.Warnings : Out.Unplaced;
			for (const TSharedPtr<FJsonValue>& Line : *Lines)
			{
				Into.Add(Line->AsString());
			}
		}
	}

	if (!R.Ok())
	{
		OutError = R.Error;
		return false;
	}
	return true;
}
