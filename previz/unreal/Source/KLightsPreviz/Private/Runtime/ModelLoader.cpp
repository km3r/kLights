#include "Runtime/ModelLoader.h"

#include "Components/StaticMeshComponent.h"
#include "Engine/CollisionProfile.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "GameFramework/Actor.h"
#include "GLTFAsset.h"
#include "GLTFMeshFactory.h"
#include "GLTFReader.h"
#include "ImageUtils.h"
#include "KLightsLog.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "MeshDescription.h"
#include "Misc/FileHelper.h"
#include "PhysicsEngine/BodySetup.h"
#include "StaticMeshAttributes.h"
#include "StaticMeshOperations.h"

namespace
{
	constexpr float MetresToCm = 100.f;

	// Parameter names on M_PrevizModel. build_assets.py creates the material;
	// these strings are the whole contract between the two.
	const FName PBaseColor(TEXT("BaseColor"));
	const FName PBaseColorMap(TEXT("BaseColorMap"));
	const FName PMetallic(TEXT("Metallic"));
	const FName PRoughness(TEXT("Roughness"));
	const FName PEmissive(TEXT("Emissive"));
	const FName PEmissiveMap(TEXT("EmissiveMap"));

	FString Describe(const TArray<GLTF::FLogMessage>& Messages, GLTF::EMessageSeverity AtLeast)
	{
		TArray<FString> Lines;
		for (const GLTF::FLogMessage& Message : Messages)
		{
			if (Message.Get<0>() >= AtLeast)
			{
				Lines.Add(Message.Get<1>().ToString());
			}
		}
		return FString::Join(Lines, TEXT("; "));
	}

	/** A glTF image as a runtime texture. Single mip: this is a previz, not a game. */
	UTexture2D* DecodeImage(const GLTF::FImage& Image, bool bSRGB, TArray<FString>& Warnings)
	{
		TArray<uint8> Bytes;
		if (Image.Data != nullptr && Image.DataByteLength > 0)
		{
			Bytes.Append(Image.Data, Image.DataByteLength);
		}
		else if (!Image.FilePath.IsEmpty())
		{
			FFileHelper::LoadFileToArray(Bytes, *Image.FilePath);
		}
		if (Bytes.IsEmpty())
		{
			Warnings.Add(FString::Printf(TEXT("image %s has no data"), *Image.Name));
			return nullptr;
		}
		UTexture2D* Texture = FImageUtils::ImportBufferAsTexture2D(Bytes);
		if (Texture == nullptr)
		{
			Warnings.Add(FString::Printf(TEXT("image %s could not be decoded"), *Image.Name));
			return nullptr;
		}
		if (!bSRGB)
		{
			Texture->SRGB = false;
			Texture->UpdateResource();
		}
		return Texture;
	}

	struct FMaterialBuilder
	{
		const GLTF::FAsset& Asset;
		UMaterialInterface* Base;
		UObject* Outer;
		TArray<FString>& Warnings;
		TMap<TPair<int32, bool>, UTexture2D*> Textures;
		TMap<int32, UMaterialInstanceDynamic*> Built;

		UTexture2D* Texture(const GLTF::FTextureMap& Map, bool bSRGB)
		{
			if (!Asset.Textures.IsValidIndex(Map.TextureIndex))
			{
				return nullptr;
			}
			const TPair<int32, bool> Key(Map.TextureIndex, bSRGB);
			if (UTexture2D** Found = Textures.Find(Key))
			{
				return *Found;
			}
			UTexture2D* Made = DecodeImage(Asset.Textures[Map.TextureIndex].Source, bSRGB, Warnings);
			Textures.Add(Key, Made);
			return Made;
		}

		/** `Index` is the glTF material index; INDEX_NONE is a primitive with none. */
		UMaterialInstanceDynamic* Get(int32 Index)
		{
			if (UMaterialInstanceDynamic** Found = Built.Find(Index))
			{
				return *Found;
			}
			UMaterialInstanceDynamic* MID = UMaterialInstanceDynamic::Create(Base, Outer);
			Built.Add(Index, MID);
			if (!Asset.Materials.IsValidIndex(Index))
			{
				return MID;
			}
			const GLTF::FMaterial& M = Asset.Materials[Index];
			if (M.ShadingModel != GLTF::FMaterial::EShadingModel::MetallicRoughness)
			{
				Warnings.Add(FString::Printf(TEXT("material %s is not metallic-roughness; drawn with its base color only"), *M.Name));
			}
			// Metallic and roughness come from their FACTORS only. A packed
			// metallic-roughness texture is linear data, and sampling it needs a
			// linear default texture the engine does not ship; for judging a look
			// in a dark room the factors are enough. Base color and emissive maps
			// are honoured.
			const FVector4f& C = M.BaseColorFactor;
			MID->SetVectorParameterValue(PBaseColor, FLinearColor(C.X, C.Y, C.Z, C.W));
			MID->SetScalarParameterValue(PMetallic, M.MetallicRoughness.MetallicFactor);
			MID->SetScalarParameterValue(PRoughness, M.MetallicRoughness.RoughnessFactor);
			const FVector3f E = M.EmissiveFactor * (M.bHasEmissiveStrength ? M.EmissiveStrength : 1.f);
			MID->SetVectorParameterValue(PEmissive, FLinearColor(E.X, E.Y, E.Z));
			if (UTexture2D* T = Texture(M.BaseColor, true))
			{
				MID->SetTextureParameterValue(PBaseColorMap, T);
			}
			if (UTexture2D* T = Texture(M.Emissive, true))
			{
				MID->SetTextureParameterValue(PEmissiveMap, T);
			}
			return MID;
		}
	};

	struct FBuiltMesh
	{
		UStaticMesh* Mesh = nullptr;
		/** glTF material index per material slot. */
		TArray<int32> SlotMaterials;
	};

	FBuiltMesh BuildMesh(const GLTF::FAsset& Asset, int32 MeshIndex, UObject* Outer, bool bCollide,
	                     int32& Triangles, TArray<FString>& Warnings)
	{
		FBuiltMesh Out;
		const GLTF::FMesh& Source = Asset.Meshes[MeshIndex];

		FMeshDescription Description;
		GLTF::FMeshFactory Factory;
		Factory.SetUniformScale(MetresToCm);
		Factory.FillMeshDescription(Source, FTransform::Identity, &Description);
		const FString Problems = Describe(Factory.GetLogMessages(), GLTF::EMessageSeverity::Warning);
		if (!Problems.IsEmpty())
		{
			Warnings.Add(FString::Printf(TEXT("mesh %s: %s"), *Source.Name, *Problems));
		}
		if (Description.Triangles().Num() == 0)
		{
			return Out;
		}
		// GLTFCore generates flat normals when a primitive has none, but never
		// tangents, and the fast build copies them verbatim. The per-vertex
		// pass asserts on per-TRIANGLE normals existing, which nothing has made
		// yet in a runtime build -- so those first.
		if (!Source.HasTangents())
		{
			FStaticMeshOperations::ComputeTriangleTangentsAndNormals(Description);
			FStaticMeshOperations::ComputeTangentsAndNormals(
				Description, EComputeNTBsFlags::Tangents | EComputeNTBsFlags::UseMikkTSpace);
		}

		FStaticMeshAttributes Attributes(Description);
		TPolygonGroupAttributesRef<FName> SlotNames = Attributes.GetPolygonGroupMaterialSlotNames();
		TArray<FStaticMaterial> Slots;
		for (const FPolygonGroupID Group : Description.PolygonGroups().GetElementIDs())
		{
			const FName Slot = SlotNames[Group];
			int32 MaterialIndex = INDEX_NONE;
			LexFromString(MaterialIndex, *Slot.ToString());
			// The slot NAME is what the fast build matches sections by. The
			// constructor's third argument only exists in editor builds, so set
			// the imported name as a field instead of passing it.
			FStaticMaterial& Made = Slots.Add_GetRef(FStaticMaterial(nullptr, Slot));
			Made.ImportedMaterialSlotName = Slot;
			// Texture-streaming UV densities are computed by an editor-only pass
			// (UStaticMesh::UpdateUVChannelData), and the renderer ensures they
			// were. These textures are transient and never stream, so any
			// initialised value is honest; 1 is the engine's own neutral.
			Made.UVChannelData = FMeshUVChannelInfo(1.f);
			Out.SlotMaterials.Add(MaterialIndex);
		}

		UStaticMesh* Mesh = NewObject<UStaticMesh>(Outer, NAME_None, RF_Transient);
		Mesh->SetStaticMaterials(Slots);
		Mesh->bAllowCPUAccess = bCollide;

		UStaticMesh::FBuildMeshDescriptionsParams Params;
		Params.bUseHashAsGuid = true;
		Params.bMarkPackageDirty = false;
		Params.bBuildSimpleCollision = false;
		Params.bCommitMeshDescription = false;
		Params.bFastBuild = true;
		Params.bAllowCpuAccess = bCollide;
		Mesh->BuildFromMeshDescriptions({&Description}, Params);
		if (Mesh->GetRenderData() == nullptr)
		{
			Warnings.Add(FString::Printf(TEXT("mesh %s built no render data"), *Source.Name));
			return Out;
		}
		if (bCollide)
		{
			if (Mesh->GetBodySetup() == nullptr)
			{
				Mesh->CreateBodySetup();
			}
			Mesh->GetBodySetup()->CollisionTraceFlag = CTF_UseComplexAsSimple;
			// Both faces block: a venue's walls are seen -- and shot at -- from
			// INSIDE, and are routinely authored as single planes facing out.
			Mesh->GetBodySetup()->bDoubleSidedGeometry = true;
		}
		Triangles += Description.Triangles().Num();
		Out.Mesh = Mesh;
		return Out;
	}
}

bool FKLightsModelLoader::Load(const FString& GlbPath, UObject* Outer, UMaterialInterface* BaseMaterial,
                               bool bCollide, FKLightsModel& Out, FString& OutError)
{
	Out = FKLightsModel();
	if (Outer == nullptr || BaseMaterial == nullptr)
	{
		OutError = TEXT("no outer or no base material");
		return false;
	}

	GLTF::FAsset Asset;
	GLTF::FFileReader Reader;
	Reader.ReadFile(GlbPath, /*bInLoadImageData=*/ true, /*bInLoadMetadata=*/ false, Asset);
	const FString Errors = Describe(Reader.GetLogMessages(), GLTF::EMessageSeverity::Error);
	if (!Errors.IsEmpty())
	{
		OutError = Errors;
		return false;
	}
	if (Asset.ValidationCheck() != GLTF::FAsset::Valid)
	{
		OutError = TEXT("the glTF failed validation");
		return false;
	}

	FMaterialBuilder Materials{Asset, BaseMaterial, Outer, Out.Warnings};
	TMap<int32, FBuiltMesh> Meshes;

	TArray<int32> Roots;
	if (Asset.Scenes.Num() > 0)
	{
		Roots = Asset.Scenes[0].Nodes;
	}
	else
	{
		Asset.GetRootNodes(Roots);
	}

	// Depth first, so every parent precedes its children -- Instantiate relies on it.
	TArray<TPair<int32, int32>> Stack;  // (glTF node, our parent)
	for (int32 i = Roots.Num() - 1; i >= 0; --i)
	{
		Stack.Emplace(Roots[i], INDEX_NONE);
	}
	while (Stack.Num() > 0)
	{
		const TPair<int32, int32> Item = Stack.Pop();
		if (!Asset.Nodes.IsValidIndex(Item.Key))
		{
			continue;
		}
		const GLTF::FNode& Node = Asset.Nodes[Item.Key];
		const int32 Mine = Out.Nodes.AddDefaulted();
		FKLightsModelNode& Made = Out.Nodes[Mine];
		Made.Name = Node.Name.IsEmpty() ? FString::Printf(TEXT("node%d"), Item.Key) : Node.Name;
		Made.Parent = Item.Value;
		Made.Local = Node.Transform;
		Made.Local.SetTranslation(Node.Transform.GetTranslation() * MetresToCm);

		if (Asset.Meshes.IsValidIndex(Node.MeshIndex))
		{
			if (Node.Type == GLTF::FNode::EType::MeshSkinned)
			{
				Out.Warnings.Add(FString::Printf(TEXT("node %s is skinned; drawn in its bind pose"), *Made.Name));
			}
			FBuiltMesh* Built = Meshes.Find(Node.MeshIndex);
			if (Built == nullptr)
			{
				Built = &Meshes.Add(Node.MeshIndex,
				                    BuildMesh(Asset, Node.MeshIndex, Outer, bCollide, Out.Triangles, Out.Warnings));
			}
			if (Built->Mesh != nullptr)
			{
				Made.Mesh.Reset(Built->Mesh);
				for (const int32 MaterialIndex : Built->SlotMaterials)
				{
					Made.Materials.Emplace(Materials.Get(MaterialIndex));
				}
			}
		}
		for (int32 c = Node.Children.Num() - 1; c >= 0; --c)
		{
			Stack.Emplace(Node.Children[c], Mine);
		}
	}

	if (Out.Nodes.Num() == 0)
	{
		OutError = TEXT("the glTF has no nodes");
		return false;
	}
	return true;
}

void FKLightsModelLoader::Instantiate(const FKLightsModel& Model, AActor* Owner, USceneComponent* Root,
                                      bool bCollide, TMap<FString, USceneComponent*>& OutByName)
{
	TArray<USceneComponent*> Made;
	Made.SetNum(Model.Nodes.Num());
	for (int32 i = 0; i < Model.Nodes.Num(); ++i)
	{
		const FKLightsModelNode& Node = Model.Nodes[i];
		USceneComponent* Component = nullptr;
		if (Node.Mesh.IsValid())
		{
			UStaticMeshComponent* MeshComponent = NewObject<UStaticMeshComponent>(Owner, NAME_None);
			MeshComponent->SetStaticMesh(Node.Mesh.Get());
			for (int32 Slot = 0; Slot < Node.Materials.Num(); ++Slot)
			{
				MeshComponent->SetMaterial(Slot, Node.Materials[Slot].Get());
			}
			if (bCollide)
			{
				MeshComponent->SetCollisionProfileName(UCollisionProfile::BlockAll_ProfileName);
			}
			else
			{
				MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			}
			Component = MeshComponent;
		}
		else
		{
			Component = NewObject<USceneComponent>(Owner, NAME_None);
		}
		Component->SetMobility(EComponentMobility::Movable);
		USceneComponent* Parent = Node.Parent != INDEX_NONE ? Made[Node.Parent] : Root;
		Component->SetupAttachment(Parent);
		Component->SetRelativeTransform(Node.Local);
		Component->RegisterComponent();
		Owner->AddInstanceComponent(Component);
		Made[i] = Component;
		OutByName.Add(Node.Name, Component);
	}
}
