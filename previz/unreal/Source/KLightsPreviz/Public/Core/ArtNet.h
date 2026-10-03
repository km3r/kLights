#pragma once

#include "CoreMinimal.h"

class FSocket;

namespace KLights::ArtNet
{
	inline constexpr int32 DefaultPort = 6454;
	inline constexpr int32 UniverseSize = 512;

	/**
	 * An ArtDmx packet's universe and payload, or false for anything else.
	 *
	 * Same wire format as shared/tools/artnet_listener.py, the reference decoder:
	 * "Art-Net\0", opcode 0x5000 little-endian at byte 8, SubUni at 14 and Net at
	 * 15 (so the 15-bit port address is Net << 8 | SubUni), payload length
	 * big-endian at 16, payload from 18.
	 */
	KLIGHTSPREVIZ_API bool ParseArtDmx(TConstArrayView<uint8> Packet, int32& OutUniverse,
	                                   TConstArrayView<uint8>& OutDmx);
}

/**
 * Listens for ArtDmx and keeps the newest frame per universe.
 *
 * Drained, not read one packet per tick: the engine sends at 40 fps and the
 * renderer may tick slower, so reading one per tick would fall further behind
 * the show every second it ran.
 */
class KLIGHTSPREVIZ_API FArtNetReceiver
{
public:
	~FArtNetReceiver();

	/** Bind. Reusable, so a second listener on the same machine can coexist. */
	bool Start(const FString& BindAddress, int32 Port, FString& OutError);
	void Stop();
	bool IsListening() const { return Socket != nullptr; }

	/** Take every pending packet. Returns how many ArtDmx packets landed. */
	int32 Drain();

	/** 512-byte buffers, keyed by 15-bit port address. Absent = never heard. */
	const TMap<int32, TArray<uint8>>& Frames() const { return Universes; }
	const uint8* Frame(int32 Universe) const;

	uint64 PacketsTotal = 0;
	/** Universes beyond this are counted but not stored, so a flood cannot grow memory. */
	static constexpr int32 MaxUniverses = 64;

private:
	FSocket* Socket = nullptr;
	TMap<int32, TArray<uint8>> Universes;
	TArray<uint8> Scratch;
};
