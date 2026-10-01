#include "Core/ArtNet.h"

#include "Common/UdpSocketBuilder.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "KLightsLog.h"
#include "Sockets.h"
#include "SocketSubsystem.h"

namespace KLights::ArtNet
{
	static const uint8 Header[8] = {'A', 'r', 't', '-', 'N', 'e', 't', 0};
	static constexpr uint16 OpDmx = 0x5000;

	bool ParseArtDmx(TConstArrayView<uint8> Packet, int32& OutUniverse, TConstArrayView<uint8>& OutDmx)
	{
		if (Packet.Num() < 18 || FMemory::Memcmp(Packet.GetData(), Header, 8) != 0)
		{
			return false;
		}
		const uint16 OpCode = uint16(Packet[8]) | (uint16(Packet[9]) << 8);
		if (OpCode != OpDmx)
		{
			return false;
		}
		const int32 Length = (int32(Packet[16]) << 8) | int32(Packet[17]);
		if (Packet.Num() < 18 + Length)
		{
			return false;
		}
		OutUniverse = (int32(Packet[15]) << 8) | int32(Packet[14]);
		OutDmx = Packet.Slice(18, FMath::Min(Length, UniverseSize));
		return true;
	}
}

FArtNetReceiver::~FArtNetReceiver()
{
	Stop();
}

bool FArtNetReceiver::Start(const FString& BindAddress, int32 Port, FString& OutError)
{
	Stop();
	FIPv4Address Address;
	if (!FIPv4Address::Parse(BindAddress, Address))
	{
		OutError = FString::Printf(TEXT("not an IPv4 address: %s"), *BindAddress);
		return false;
	}
	Socket = FUdpSocketBuilder(TEXT("kLights Art-Net"))
		.AsNonBlocking()
		.AsReusable()
		.BoundToAddress(Address)
		.BoundToPort(Port)
		.WithReceiveBufferSize(1 << 20)
		.Build();
	if (Socket == nullptr)
	{
		OutError = FString::Printf(TEXT("could not bind UDP %s:%d"), *BindAddress, Port);
		return false;
	}
	Scratch.SetNumUninitialized(2048);
	return true;
}

void FArtNetReceiver::Stop()
{
	if (Socket != nullptr)
	{
		Socket->Close();
		ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Socket);
		Socket = nullptr;
	}
}

const uint8* FArtNetReceiver::Frame(int32 Universe) const
{
	const TArray<uint8>* Buffer = Universes.Find(Universe);
	return Buffer ? Buffer->GetData() : nullptr;
}

int32 FArtNetReceiver::Drain()
{
	if (Socket == nullptr)
	{
		return 0;
	}
	int32 Landed = 0;
	uint32 Pending = 0;
	while (Socket->HasPendingData(Pending))
	{
		int32 Read = 0;
		if (!Socket->Recv(Scratch.GetData(), Scratch.Num(), Read) || Read <= 0)
		{
			break;
		}
		int32 Universe = 0;
		TConstArrayView<uint8> Dmx;
		if (!KLights::ArtNet::ParseArtDmx(TConstArrayView<uint8>(Scratch.GetData(), Read), Universe, Dmx))
		{
			continue;
		}
		++PacketsTotal;
		++Landed;
		TArray<uint8>* Buffer = Universes.Find(Universe);
		if (Buffer == nullptr)
		{
			if (Universes.Num() >= MaxUniverses)
			{
				continue;
			}
			Buffer = &Universes.Add(Universe);
			Buffer->SetNumZeroed(KLights::ArtNet::UniverseSize);
		}
		FMemory::Memcpy(Buffer->GetData(), Dmx.GetData(), Dmx.Num());
	}
	return Landed;
}
