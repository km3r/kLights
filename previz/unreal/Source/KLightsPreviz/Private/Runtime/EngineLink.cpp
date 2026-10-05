#include "Runtime/EngineLink.h"

#include "HAL/FileManager.h"
#include "HttpModule.h"
#include "Interfaces/IHttpResponse.h"
#include "KLightsLog.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace
{
	FString& CacheDir()
	{
		static FString Dir = FPaths::ProjectSavedDir() / TEXT("ModelCache");
		return Dir;
	}
}

FString FKLightsEngineLink::CachePath(const FString& Sha)
{
	return CacheDir() / (Sha + TEXT(".glb"));
}

void FKLightsEngineLink::SetCacheDir(const FString& Dir)
{
	CacheDir() = Dir;
}

void FKLightsEngineLink::Start(const FString& InBaseUrl)
{
	Stop();
	BaseUrl = InBaseUrl;
	BaseUrl.RemoveFromEnd(TEXT("/"));
	Etag.Reset();
	DeliveredRev.Reset();
	State = EState::Connecting;
	Message = TEXT("waiting for the engine");
	NextPoll = 0.0;
	UE_LOG(LogKLights, Display, TEXT("previz scene from %s/api/previz/scene"), *BaseUrl);
}

bool FKLightsEngineLink::LoadFile(const FString& Path, FString& OutError)
{
	Stop();
	FString Json;
	if (!FFileHelper::LoadFileToString(Json, *Path))
	{
		OutError = FString::Printf(TEXT("cannot read %s"), *Path);
		return false;
	}
	FKLightsScene Scene;
	if (!ParseKLightsScene(Json, Scene, OutError))
	{
		return false;
	}
	State = EState::Offline;
	Message = FString::Printf(TEXT("loaded %s"), *FPaths::GetCleanFilename(Path));
	Pending = MoveTemp(Scene);
	// Offline means no engine to fetch from: whatever is already cached is all there is.
	MissingModels = 0;
	Deliver();
	return true;
}

void FKLightsEngineLink::Stop()
{
	if (Request.IsValid())
	{
		Request->OnProcessRequestComplete().Unbind();
		Request->CancelRequest();
		Request.Reset();
	}
	bInFlight = false;
	Pending.Reset();
	ToFetch.Reset();
	State = EState::Idle;
}

void FKLightsEngineLink::Tick(double Now)
{
	if (bInFlight || BaseUrl.IsEmpty() || State == EState::Offline || State == EState::Idle)
	{
		return;
	}
	if (Now >= NextPoll)
	{
		NextPoll = Now + PollSeconds;
		Poll();
	}
}

void FKLightsEngineLink::Poll()
{
	Request = FHttpModule::Get().CreateRequest();
	Request->SetURL(BaseUrl + TEXT("/api/previz/scene"));
	Request->SetVerb(TEXT("GET"));
	Request->SetTimeout(TimeoutSeconds);
	if (!Etag.IsEmpty())
	{
		Request->SetHeader(TEXT("If-None-Match"), Etag);
	}
	Request->OnProcessRequestComplete().BindLambda(
		[this](FHttpRequestPtr, FHttpResponsePtr Response, bool bConnected)
		{
			bInFlight = false;
			OnSceneResponse(Response, bConnected);
		});
	bInFlight = true;
	Request->ProcessRequest();
}

void FKLightsEngineLink::OnSceneResponse(FHttpResponsePtr Response, bool bConnected)
{
	if (!bConnected || !Response.IsValid())
	{
		if (State != EState::Unreachable)
		{
			UE_LOG(LogKLights, Warning, TEXT("engine at %s is not answering; keeping the last scene"), *BaseUrl);
		}
		State = EState::Unreachable;
		Message = TEXT("not answering -- is `python -m engine.server` running?");
		return;
	}
	LastContact = FPlatformTime::Seconds();
	const int32 Code = Response->GetResponseCode();
	if (Code == 304)
	{
		if (State != EState::Connected && !Pending.IsSet())
		{
			State = EState::Connected;
			Message.Reset();
		}
		return;
	}
	if (Code != 200)
	{
		State = EState::Rejected;
		Message = FString::Printf(TEXT("HTTP %d: %s"), Code, *Response->GetContentAsString().Left(200));
		UE_LOG(LogKLights, Warning, TEXT("engine refused the scene: %s"), *Message);
		return;
	}

	FKLightsScene Scene;
	FString Error;
	if (!ParseKLightsScene(Response->GetContentAsString(), Scene, Error))
	{
		State = EState::Rejected;
		Message = Error;
		UE_LOG(LogKLights, Error, TEXT("the engine's scene is unusable: %s"), *Error);
		// Do not remember the ETag: a fixed engine should be retried, not 304'd.
		return;
	}
	Etag = Response->GetHeader(TEXT("ETag"));
	State = EState::Connected;
	Message.Reset();

	ToFetch.Reset();
	MissingModels = 0;
	Fetched = 0;
	for (const TPair<FString, FString>& Asset : Scene.AssetNames)
	{
		if (!FPaths::FileExists(CachePath(Asset.Key)))
		{
			ToFetch.Add(Asset.Key);
		}
	}
	if (Scene.Rev == DeliveredRev && ToFetch.Num() == 0)
	{
		return;   // a retry, and every model has turned up some other way
	}
	Pending = MoveTemp(Scene);
	UE_LOG(LogKLights, Display, TEXT("scene %s: %s in %s, %d fixture(s), %d model(s) to fetch"),
	       *Pending->Rev, *Pending->Event, *Pending->Venue, Pending->Fixtures.Num(), ToFetch.Num());
	FetchNextModel();
}

void FKLightsEngineLink::FetchNextModel()
{
	if (ToFetch.Num() == 0)
	{
		Finish();
		return;
	}
	const FString Sha = ToFetch.Pop();
	bInFlight = true;
	Request = FHttpModule::Get().CreateRequest();
	Request->SetURL(FString::Printf(TEXT("%s/api/previz/model/%s.glb"), *BaseUrl, *Sha));
	Request->SetVerb(TEXT("GET"));
	Request->SetTimeout(ModelTimeoutSeconds);
	Request->OnProcessRequestComplete().BindLambda(
		[this, Sha](FHttpRequestPtr, FHttpResponsePtr Response, bool bConnected)
		{
			bInFlight = false;
			if (bConnected && Response.IsValid() && Response->GetResponseCode() == 200)
			{
				// Written to a temporary name and moved, so a fetch cut off halfway
				// never leaves a truncated file that looks cached.
				const FString Final = CachePath(Sha);
				const FString Partial = Final + TEXT(".part");
				if (FFileHelper::SaveArrayToFile(Response->GetContent(), *Partial)
					&& IFileManager::Get().Move(*Final, *Partial, true, true))
				{
					++Fetched;
					UE_LOG(LogKLights, Display, TEXT("fetched model %s (%d bytes)"), *Sha.Left(12),
					       Response->GetContent().Num());
				}
				else
				{
					++MissingModels;
				}
			}
			else
			{
				++MissingModels;
				UE_LOG(LogKLights, Warning, TEXT("could not fetch model %s"), *Sha.Left(12));
			}
			if (Pending.IsSet())
			{
				FetchNextModel();
			}
		});
	Request->ProcessRequest();
}

void FKLightsEngineLink::Finish()
{
	if (MissingModels > 0)
	{
		// Forget the ETag so the next poll is a full answer, and the models
		// still missing are asked for again. Not every second: a model that is
		// failing is usually failing for a reason that takes a while to fix.
		Etag.Reset();
		NextPoll = FPlatformTime::Seconds() + RetrySeconds;
	}
	if (Pending.IsSet() && Pending->Rev == DeliveredRev && Fetched == 0)
	{
		Pending.Reset();   // the same scene with nothing new: rebuilding would only re-home the heads
		return;
	}
	Deliver();
}

void FKLightsEngineLink::Deliver()
{
	if (!Pending.IsSet())
	{
		return;
	}
	FKLightsScene Scene = MoveTemp(Pending.GetValue());
	Pending.Reset();
	DeliveredRev = Scene.Rev;
	if (OnScene)
	{
		OnScene(Scene);
	}
}
