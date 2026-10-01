// The C++ held to what the Python says.
//
//   KLights.Parity.*  engine/tests/data/previz_parity.json -- the decode the app
//                     shares with the show. A STANDING guard: the Python is the
//                     definition, and gen_previz_parity.py --check keeps the
//                     vectors current in CI.
//   KLights.Look.*    previz/unreal/Tests/look_fixture.json -- the mirror ball and
//                     beam stand-ins, frozen once from the retired editor driver.
//
// Run headless from the repo:
//   UnrealEditor-Cmd previz/unreal/KLightsPreviz.uproject
//       -ExecCmds="Automation RunTests KLights; Quit" -unattended -nullrhi
// or `python previz/build.py test`.

#include "Core/Decode.h"
#include "Core/Optics.h"
#include "Dom/JsonObject.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Runtime/Scene.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	constexpr EAutomationTestFlags Flags = EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter;

	FString RepoPath(const FString& Relative)
	{
		// previz/unreal/ is the project; the repo is two up.
		return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../..") / Relative);
	}

	TSharedPtr<FJsonObject> LoadJson(FAutomationTestBase& Test, const FString& Relative)
	{
		FString Text;
		const FString Path = RepoPath(Relative);
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			Test.AddError(FString::Printf(TEXT("cannot read %s"), *Path));
			return nullptr;
		}
		TSharedPtr<FJsonObject> Root;
		if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root.IsValid())
		{
			Test.AddError(FString::Printf(TEXT("%s is not valid JSON"), *Path));
			return nullptr;
		}
		return Root;
	}

	TSharedPtr<FJsonObject> Parity(FAutomationTestBase& Test)
	{
		return LoadJson(Test, TEXT("engine/tests/data/previz_parity.json"));
	}

	TSharedPtr<FJsonObject> Look(FAutomationTestBase& Test)
	{
		return LoadJson(Test, TEXT("previz/unreal/Tests/look_fixture.json"));
	}

	FVector Vec(const TArray<TSharedPtr<FJsonValue>>& A, int32 At = 0)
	{
		return FVector(A[At]->AsNumber(), A[At + 1]->AsNumber(), A[At + 2]->AsNumber());
	}

	FVector Vec(const TSharedPtr<FJsonValue>& V)
	{
		return Vec(V->AsArray());
	}

	/** Worst |a - b|, accumulated across a test so one line reports the whole sweep. */
	struct FWorst
	{
		double Value = 0.0;
		FString Where;
		void See(double A, double B, const FString& At)
		{
			const double D = FMath::Abs(A - B);
			if (!(D <= Value))   // also catches NaN
			{
				Value = FMath::IsNaN(D) ? TNumericLimits<double>::Max() : D;
				Where = At;
			}
		}
		void See(const FVector& A, const FVector& B, const FString& At)
		{
			See(A.X, B.X, At);
			See(A.Y, B.Y, At);
			See(A.Z, B.Z, At);
		}
		void Check(FAutomationTestBase& Test, const TCHAR* What, double Tolerance) const
		{
			if (Value > Tolerance)
			{
				Test.AddError(FString::Printf(TEXT("%s: worst disagreement %.3g at %s (tolerance %.0g)"),
				                              What, Value, *Where, Tolerance));
			}
			else
			{
				Test.AddInfo(FString::Printf(TEXT("%s: worst disagreement %.3g"), What, Value));
			}
		}
	};

	KLights::FDecodeFrame DecodeFrom(const TSharedPtr<FJsonObject>& D)
	{
		KLights::FDecodeFrame F;
		F.bBearingIsPan = D->GetStringField(TEXT("bearing_channel")) == TEXT("pan");
		F.bElevationIsPan = D->GetStringField(TEXT("elevation_channel")) == TEXT("pan");
		F.BearingMax = D->GetNumberField(TEXT("bearing_max"));
		F.ElevationMax = D->GetNumberField(TEXT("elevation_max"));
		F.bBearingInvert = D->GetBoolField(TEXT("bearing_invert"));
		F.bElevationInvert = D->GetBoolField(TEXT("elevation_invert"));
		F.bElevationCentred = D->GetStringField(TEXT("elevation_anchor")) == TEXT("center");
		F.ElevationOffset = D->GetNumberField(TEXT("elevation_offset"));
		F.MountFacing = D->GetNumberField(TEXT("mount_facing"));
		F.Half = D->GetNumberField(TEXT("half"));
		F.Span = D->GetNumberField(TEXT("span"));
		return F;
	}
}

// --------------------------------------------------------------- the decode --

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FKLightsParityDecode, "KLights.Parity.Decode", Flags)
bool FKLightsParityDecode::RunTest(const FString&)
{
	const TSharedPtr<FJsonObject> Root = Parity(*this);
	if (!Root.IsValid())
	{
		return false;
	}
	FWorst Angles, Directions;
	int32 Cases = 0;
	for (const TSharedPtr<FJsonValue>& Head : Root->GetArrayField(TEXT("decode")))
	{
		const TSharedPtr<FJsonObject> H = Head->AsObject();
		const KLights::FDecodeFrame Frame = DecodeFrom(H->GetObjectField(TEXT("decode")));
		const FString Name = H->GetStringField(TEXT("name"));
		for (const TSharedPtr<FJsonValue>& Case : H->GetArrayField(TEXT("cases")))
		{
			const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
			double Bearing, Elevation;
			KLights::DecodeAim(Frame, C[0]->AsNumber(), C[1]->AsNumber(), Bearing, Elevation);
			const FString At = FString::Printf(TEXT("%s pan=%d tilt=%d"), *Name, int32(C[0]->AsNumber()), int32(C[1]->AsNumber()));
			Angles.See(Bearing, C[2]->AsNumber(), At);
			Angles.See(Elevation, C[3]->AsNumber(), At);
			Directions.See(KLights::BeamDirection(Bearing, Elevation), Vec(C, 4), At);
			++Cases;
		}
	}
	AddInfo(FString::Printf(TEXT("%d poses"), Cases));
	TestTrue(TEXT("the parity file has decode cases"), Cases > 1000);
	Angles.Check(*this, TEXT("bearing/elevation"), 1e-9);
	Directions.Check(*this, TEXT("beam direction"), 1e-9);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FKLightsParityServo, "KLights.Parity.Servo", Flags)
bool FKLightsParityServo::RunTest(const FString&)
{
	const TSharedPtr<FJsonObject> Root = Parity(*this);
	if (!Root.IsValid())
	{
		return false;
	}
	FWorst Worst;
	int32 Steps = 0;
	for (const TSharedPtr<FJsonValue>& Run : Root->GetArrayField(TEXT("servo")))
	{
		const TSharedPtr<FJsonObject> R = Run->AsObject();
		const TArray<TSharedPtr<FJsonValue>>& Rates = R->GetArrayField(TEXT("rates"));
		KLights::FServo Servo;
		Servo.PanRate = Rates[0]->AsNumber();
		Servo.TiltRate = Rates[1]->AsNumber();
		for (const TSharedPtr<FJsonValue>& Step : R->GetArrayField(TEXT("steps")))
		{
			const TArray<TSharedPtr<FJsonValue>>& S = Step->AsArray();
			const FVector2D At = Servo.Follow(S[0]->AsNumber(), S[1]->AsNumber(), S[2]->AsNumber());
			Worst.See(At.X, S[3]->AsNumber(), FString::Printf(TEXT("step %d pan"), Steps));
			Worst.See(At.Y, S[4]->AsNumber(), FString::Printf(TEXT("step %d tilt"), Steps));
			++Steps;
		}
	}
	TestTrue(TEXT("the parity file has servo steps"), Steps > 100);
	Worst.Check(*this, TEXT("yoke position, words"), 1e-9);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FKLightsParityColor, "KLights.Parity.Color", Flags)
bool FKLightsParityColor::RunTest(const FString&)
{
	const TSharedPtr<FJsonObject> Root = Parity(*this);
	if (!Root.IsValid())
	{
		return false;
	}
	FWorst Worst;
	int32 Cases = 0, SplitWrong = 0;
	for (const TSharedPtr<FJsonValue>& Entry : Root->GetArrayField(TEXT("color")))
	{
		const TSharedPtr<FJsonObject> E = Entry->AsObject();
		const TSharedPtr<FJsonObject> Fixture = E->GetObjectField(TEXT("fixture"));
		const KLights::FChannels Channels = ParseKLightsChannels(Fixture->GetObjectField(TEXT("channels")));
		TArray<KLights::FColorSlot> Slots;
		TestTrue(TEXT("colour slots parse"), ParseKLightsColorSlots(Fixture->GetArrayField(TEXT("color_slots")), Slots));
		for (const TSharedPtr<FJsonValue>& Case : E->GetArrayField(TEXT("cases")))
		{
			const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
			uint8 Frame[512] = {};
			for (const TSharedPtr<FJsonValue>& Pair : C[0]->AsArray())
			{
				const TArray<TSharedPtr<FJsonValue>>& P = Pair->AsArray();
				Frame[int32(P[0]->AsNumber())] = uint8(P[1]->AsNumber());
			}
			const KLights::FOutput Out = KLights::FixtureOutput(Channels, Slots, Frame);
			const FString At = FString::Printf(TEXT("%s case %d"), *E->GetStringField(TEXT("name")), Cases);
			Worst.See(Out.Level, C[1]->AsNumber(), At);
			Worst.See(Out.Color, Vec(C[2]), At);
			const bool bWantSplit = C[3]->Type != EJson::Null;
			if (bWantSplit != Out.bSplit)
			{
				++SplitWrong;
			}
			else if (bWantSplit)
			{
				const TArray<TSharedPtr<FJsonValue>>& Halves = C[3]->AsArray();
				Worst.See(Out.Top, Vec(Halves[0]), At + TEXT(" top"));
				Worst.See(Out.Bottom, Vec(Halves[1]), At + TEXT(" bottom"));
			}
			++Cases;
		}
	}
	TestTrue(TEXT("the parity file has colour cases"), Cases > 1000);
	TestEqual(TEXT("split / not split agrees in every case"), SplitWrong, 0);
	Worst.Check(*this, TEXT("level and colour"), 1e-9);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FKLightsParityWords, "KLights.Parity.Words", Flags)
bool FKLightsParityWords::RunTest(const FString&)
{
	const TSharedPtr<FJsonObject> Root = Parity(*this);
	if (!Root.IsValid())
	{
		return false;
	}
	for (const TSharedPtr<FJsonValue>& Case : Root->GetArrayField(TEXT("words")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		uint8 Frame[512] = {};
		Frame[0] = uint8(C[0]->AsNumber());
		Frame[1] = uint8(C[1]->AsNumber());
		Frame[2] = uint8(C[0]->AsNumber());
		TestEqual(TEXT("coarse+fine"), KLights::ChannelWord(Frame, 0, 1), int32(C[2]->AsNumber()));
		TestEqual(TEXT("coarse only"), KLights::ChannelWord(Frame, 2, INDEX_NONE), int32(C[3]->AsNumber()));
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FKLightsParityManifest, "KLights.Parity.Manifest", Flags)
bool FKLightsParityManifest::RunTest(const FString&)
{
	const TSharedPtr<FJsonObject> Root = Parity(*this);
	if (!Root.IsValid())
	{
		return false;
	}
	FString Json;
	FJsonSerializer::Serialize(Root->GetObjectField(TEXT("manifest")).ToSharedRef(), TJsonWriterFactory<>::Create(&Json));
	FKLightsScene Scene;
	FString Error;
	if (!TestTrue(FString::Printf(TEXT("the sample event's manifest parses (%s)"), *Error),
	              ParseKLightsScene(Json, Scene, Error)))
	{
		AddError(Error);
		return false;
	}
	TestEqual(TEXT("event"), Scene.Event, TEXT("sample"));
	TestEqual(TEXT("four placed fixtures"), Scene.Fixtures.Num(), 4);
	TestEqual(TEXT("two movers"), Scene.Fixtures.FilterByPredicate([](const FKLightsFixture& F) { return F.bMover; }).Num(), 2);
	TestTrue(TEXT("the room is [X=depth, Y=width, Z=height]"), Scene.RoomSize.Equals(FVector(800, 1200, 450)));
	TestEqual(TEXT("one unplaced"), Scene.Unplaced.Num(), 1);
	if (TestEqual(TEXT("one set piece"), Scene.Models.Num(), 1))
	{
		TestTrue(TEXT("its model is a listed asset"), Scene.AssetNames.Contains(Scene.Models[0].Model));
	}
	TestEqual(TEXT("views lead with overview"), Scene.Views[0].Name, TEXT("overview"));
	TestEqual(TEXT("hung mode decodes elevation from tilt"), Scene.Fixtures[0].Decode.bElevationIsPan, false);
	TestEqual(TEXT("the room's own fog"), Scene.Optics.FogDensity, 0.25);

	// The manifest is strict: drop one optics key and the parse names it.
	TSharedPtr<FJsonObject> Broken = Root->GetObjectField(TEXT("manifest"));
	Broken->GetObjectField(TEXT("optics"))->RemoveField(TEXT("beam_gain"));
	FString BrokenJson;
	FJsonSerializer::Serialize(Broken.ToSharedRef(), TJsonWriterFactory<>::Create(&BrokenJson));
	FKLightsScene Ignored;
	TestFalse(TEXT("a missing optics key fails the parse"), ParseKLightsScene(BrokenJson, Ignored, Error));
	TestTrue(TEXT("...and says which"), Error.Contains(TEXT("beam_gain")));
	return true;
}

// ------------------------------------------------------------------ the look --

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FKLightsLookBall, "KLights.Look.MirrorBall", Flags)
bool FKLightsLookBall::RunTest(const FString&)
{
	using namespace KLights::Ball;
	const TSharedPtr<FJsonObject> Root = Look(*this);
	if (!Root.IsValid())
	{
		return false;
	}
	for (const TSharedPtr<FJsonValue>& Case : Root->GetArrayField(TEXT("lattice")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		const FIntPoint Got = Lattice(C[0]->AsNumber(), C[1]->AsNumber());
		TestEqual(TEXT("lattice segments"), Got.X, int32(C[2]->AsNumber()));
		TestEqual(TEXT("lattice rings"), Got.Y, int32(C[3]->AsNumber()));
	}

	FWorst Normals;
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Root->GetObjectField(TEXT("fibonacci"))->Values)
	{
		const TArray<TSharedPtr<FJsonValue>>& Want = Pair.Value->AsArray();
		const TArray<FVector> Got = FibonacciNormals(FCString::Atoi(*Pair.Key));
		TestEqual(TEXT("fibonacci count"), Got.Num(), Want.Num());
		for (int32 i = 0; i < FMath::Min(Got.Num(), Want.Num()); ++i)
		{
			Normals.See(Got[i], Vec(Want[i]), FString::Printf(TEXT("fibonacci %s #%d"), *Pair.Key, i));
		}
	}
	const TSharedPtr<FJsonObject> Tiles = Root->GetObjectField(TEXT("tiles"));
	const TArray<FTile> GotTiles = FacetTiles(Tiles->GetNumberField(TEXT("radius")),
	                                          int32(Tiles->GetNumberField(TEXT("segments"))),
	                                          int32(Tiles->GetNumberField(TEXT("rings"))));
	const TArray<TSharedPtr<FJsonValue>>& WantTiles = Tiles->GetArrayField(TEXT("tiles"));
	TestEqual(TEXT("tile count"), GotTiles.Num(), WantTiles.Num());
	for (int32 i = 0; i < FMath::Min(GotTiles.Num(), WantTiles.Num()); ++i)
	{
		const TArray<TSharedPtr<FJsonValue>>& W = WantTiles[i]->AsArray();
		const FString At = FString::Printf(TEXT("tile %d"), i);
		Normals.See(GotTiles[i].Normal, Vec(W, 0), At);
		Normals.See(GotTiles[i].East, Vec(W, 3), At);
		Normals.See(GotTiles[i].Width, W[6]->AsNumber(), At);
		Normals.See(GotTiles[i].Height, W[7]->AsNumber(), At);
	}
	const TArray<FVector> Base = FibonacciNormals(50);
	for (const TSharedPtr<FJsonValue>& Case : Root->GetArrayField(TEXT("spin")))
	{
		const TSharedPtr<FJsonObject> C = Case->AsObject();
		TArray<FVector> Spun;
		Spin(Base, C->GetNumberField(TEXT("angle")), Spun);
		const TArray<TSharedPtr<FJsonValue>>& Want = C->GetArrayField(TEXT("normals"));
		for (int32 i = 0; i < Want.Num(); ++i)
		{
			Normals.See(Spun[i], Vec(Want[i]), FString::Printf(TEXT("spin %g #%d"), C->GetNumberField(TEXT("angle")), i));
		}
	}
	for (const TSharedPtr<FJsonValue>& Case : Root->GetArrayField(TEXT("facet_size")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		Normals.See(FacetSize(C[0]->AsNumber(), int32(C[1]->AsNumber())), C[2]->AsNumber(), TEXT("facet size"));
	}
	Normals.Check(*this, TEXT("lattices, normals, tiles"), 1e-9);

	const TSharedPtr<FJsonObject> Exits = Root->GetObjectField(TEXT("room_exit"));
	const FVector Room = Vec(Exits->GetArrayField(TEXT("room")));
	FWorst ExitWorst;
	int32 ExitWrong = 0;
	for (const TSharedPtr<FJsonValue>& Case : Exits->GetArrayField(TEXT("cases")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		FVector Point, Normal;
		const bool bGot = RayRoomExit(Vec(C[0]), Vec(C[1]), Room, Point, Normal);
		if (bGot != (C[2]->Type != EJson::Null))
		{
			++ExitWrong;
			continue;
		}
		if (bGot)
		{
			const TArray<TSharedPtr<FJsonValue>>& W = C[2]->AsArray();
			ExitWorst.See(Point, Vec(W[0]), TEXT("room exit point"));
			ExitWorst.See(Normal, Vec(W[1]), TEXT("room exit normal"));
		}
	}
	TestEqual(TEXT("room exit found / not found agrees"), ExitWrong, 0);
	ExitWorst.Check(*this, TEXT("room exits, mm"), 1e-6);

	const TSharedPtr<FJsonObject> Hits = Root->GetObjectField(TEXT("beam_hits_ball"));
	const FVector BallAt = Vec(Hits->GetArrayField(TEXT("ball")));
	const double BallR = Hits->GetNumberField(TEXT("radius"));
	int32 HitWrong = 0;
	for (const TSharedPtr<FJsonValue>& Case : Hits->GetArrayField(TEXT("cases")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		HitWrong += BeamHitsBall(Vec(C[0]), Vec(C[1]), C[2]->AsNumber(), BallAt, BallR) != C[3]->AsBool();
	}
	TestEqual(TEXT("beam-hits-ball agrees"), HitWrong, 0);
	for (const TSharedPtr<FJsonValue>& Case : Root->GetArrayField(TEXT("lit_facets")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		TestEqual(TEXT("lit facets"), LitFacets(C[0]->AsNumber(), C[1]->AsNumber(), C[2]->AsNumber(), int32(C[3]->AsNumber())),
		          int32(C[4]->AsNumber()));
	}

	const TSharedPtr<FJsonObject> DotSet = Root->GetObjectField(TEXT("dots"));
	const FVector Ball = Vec(DotSet->GetArrayField(TEXT("ball")));
	const double Radius = DotSet->GetNumberField(TEXT("radius"));
	const FVector DotRoom = Vec(DotSet->GetArrayField(TEXT("room")));
	const TArray<FVector> Facets = FibonacciNormals(int32(DotSet->GetNumberField(TEXT("count"))));
	FWorst DotWorst;
	int32 CountWrong = 0, Total = 0;
	for (const TSharedPtr<FJsonValue>& Case : DotSet->GetArrayField(TEXT("cases")))
	{
		const TSharedPtr<FJsonObject> C = Case->AsObject();
		TArray<FVector> Spun;
		Spin(Facets, C->GetNumberField(TEXT("angle")), Spun);
		TArray<FDot> Got;
		Dots(Vec(C->GetArrayField(TEXT("origin"))), Vec(C->GetArrayField(TEXT("direction"))),
		     C->GetNumberField(TEXT("half")), Ball, Radius, DotRoom, Spun,
		     DotSet->GetNumberField(TEXT("aperture")), int32(C->GetNumberField(TEXT("stride"))), Got);
		const TArray<TSharedPtr<FJsonValue>>& Want = C->GetArrayField(TEXT("dots"));
		if (Got.Num() != Want.Num())
		{
			++CountWrong;
			AddError(FString::Printf(TEXT("dot count %d, Python had %d"), Got.Num(), Want.Num()));
			continue;
		}
		for (int32 i = 0; i < Want.Num(); ++i)
		{
			const TArray<TSharedPtr<FJsonValue>>& W = Want[i]->AsArray();
			const FString At = FString::Printf(TEXT("dot %d"), Total + i);
			DotWorst.See(Got[i].Facet, Vec(W, 0), At);
			DotWorst.See(Got[i].Point, Vec(W, 3), At);
			DotWorst.See(Got[i].Normal, Vec(W, 6), At);
			DotWorst.See(Got[i].Throw, W[9]->AsNumber(), At);
			DotWorst.See(Got[i].Spot, W[10]->AsNumber(), At);
		}
		Total += Want.Num();
	}
	TestEqual(TEXT("every configuration lights the same facets"), CountWrong, 0);
	TestTrue(TEXT("the fixture has reflections in it"), Total > 1000);
	DotWorst.Check(*this, TEXT("mirror-ball dots, mm"), 1e-6);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FKLightsLookBeams, "KLights.Look.Beams", Flags)
bool FKLightsLookBeams::RunTest(const FString&)
{
	using namespace KLights::Look;
	const TSharedPtr<FJsonObject> Root = Look(*this);
	if (!Root.IsValid())
	{
		return false;
	}
	const TSharedPtr<FJsonObject> Clear = Root->GetObjectField(TEXT("clearance"));
	const FVector Centre = Vec(Clear->GetArrayField(TEXT("centre")));
	const double Radius = Clear->GetNumberField(TEXT("radius"));
	FWorst Worst;
	int32 Partial = 0;
	for (const TSharedPtr<FJsonValue>& Case : Clear->GetArrayField(TEXT("cases")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		const double Want = C[3]->AsNumber();
		Partial += Want > 0.0 && Want < 1.0;
		Worst.See(BallClearance(Vec(C[0]), Vec(C[1]), C[2]->AsNumber(), Centre, Radius), Want, TEXT("clearance"));
	}
	TestTrue(TEXT("the fixture exercises partial clearance"), Partial > 20);
	for (const TSharedPtr<FJsonValue>& Case : Root->GetArrayField(TEXT("split_plane")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		Worst.See(SplitPlane(Vec(C[0])), Vec(C[1]), TEXT("split plane"));
	}
	for (const TSharedPtr<FJsonValue>& Case : Root->GetArrayField(TEXT("candela")))
	{
		const TArray<TSharedPtr<FJsonValue>>& C = Case->AsArray();
		const double Want = C[2]->AsNumber();
		Worst.See(Candela(C[0]->AsNumber(), C[1]->AsNumber()) / Want, 1.0, TEXT("candela (relative)"));
	}
	Worst.Check(*this, TEXT("clearance, split plane, candela"), 1e-9);
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
