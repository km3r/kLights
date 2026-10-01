#pragma once

#include "CoreMinimal.h"
#include "Interfaces/IHttpRequest.h"
#include "Runtime/Scene.h"

/**
 * The app's one connection to the show engine: GET /api/previz/scene once a
 * second, and the models a new scene names.
 *
 * Polled with If-None-Match, so an unchanged scene costs a 304 and nothing
 * else. A WebSocket would add nothing -- the scene changes when someone edits
 * the patch, not 40 times a second. Art-Net is the real-time link, and it does
 * not come through here at all.
 *
 * A scene is DELIVERED only once every model it names is on disk (in
 * Saved/ModelCache, named by hash), so the stage is never built half-dressed.
 * A model that cannot be fetched is reported and the scene delivered anyway:
 * a missing set piece must not stop the room from appearing.
 *
 * If the engine goes away, the last scene stays up and the status says so.
 */
class KLIGHTSPREVIZ_API FKLightsEngineLink
{
public:
	enum class EState : uint8 { Idle, Connecting, Connected, Unreachable, Rejected, Offline };

	/** Called on the game thread with each new scene, models already cached. */
	TFunction<void(const FKLightsScene&)> OnScene;

	~FKLightsEngineLink() { Stop(); }

	void Start(const FString& InBaseUrl);
	/** Load one scene from a file instead (tests, stills). No polling. */
	bool LoadFile(const FString& Path, FString& OutError);
	void Stop();
	void Tick(double Now);

	EState GetState() const { return State; }
	const FString& GetBaseUrl() const { return BaseUrl; }
	const FString& GetMessage() const { return Message; }
	double GetLastContact() const { return LastContact; }
	int32 GetMissingModels() const { return MissingModels; }

	/** Where a model with this hash lives on disk once fetched. */
	static FString CachePath(const FString& Sha);
	/** Use another cache directory (-ModelCache=): an offline bundle's models. */
	static void SetCacheDir(const FString& Dir);

private:
	void Poll();
	void OnSceneResponse(FHttpResponsePtr Response, bool bConnected);
	void FetchNextModel();
	void Deliver();

	FString BaseUrl;
	EState State = EState::Idle;
	FString Message;
	FString Etag;
	double NextPoll = 0.0;
	double LastContact = 0.0;
	bool bInFlight = false;
	TSharedPtr<IHttpRequest, ESPMode::ThreadSafe> Request;

	/** The scene waiting for its models. */
	TOptional<FKLightsScene> Pending;
	TArray<FString> ToFetch;
	int32 MissingModels = 0;

	static constexpr double PollSeconds = 1.0;
	static constexpr float TimeoutSeconds = 3.f;
	static constexpr float ModelTimeoutSeconds = 60.f;
};
