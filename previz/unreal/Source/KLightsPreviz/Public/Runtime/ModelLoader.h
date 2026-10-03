#pragma once

#include "CoreMinimal.h"
#include "UObject/StrongObjectPtr.h"

class AActor;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class USceneComponent;
class UStaticMesh;

/** One glTF node: where it sits under its parent, and what it draws. */
struct FKLightsModelNode
{
	FString Name;
	int32 Parent = INDEX_NONE;
	/** Local to the parent, Unreal axes and centimetres. */
	FTransform Local;
	/** Null for a pure transform node (a pivot such as a yoke or a lens). */
	TStrongObjectPtr<UStaticMesh> Mesh;
	/** Per material slot of `Mesh`, in slot order. */
	TArray<TStrongObjectPtr<UMaterialInstanceDynamic>> Materials;
};

/** A loaded .glb, ready to instantiate any number of times. */
struct FKLightsModel
{
	TArray<FKLightsModelNode> Nodes;
	TArray<FString> Warnings;
	int32 Triangles = 0;
};

/**
 * Self-contained GLB in, runtime static meshes out -- in a cooked game, with
 * no Interchange pipeline assets and no editor.
 *
 * The recipe is Epic's own runtime path (InterchangeStaticMeshFactory.cpp:936):
 * a fast build from a MeshDescription with CPU access on BOTH the mesh and the
 * index buffer. Two things it gets wrong if left to defaults, both silent:
 *
 *   * The mesh's Outer must resolve to a GAME world. Runtime trimesh cooking only
 *     happens when UBodySetup::IsRuntime() sees one (BodySetup.cpp:403), and
 *     the attempt latches, so a mesh created in the transient package simply
 *     never collides and never says so.
 *   * Material slots must exist BEFORE the build, named the way GLTFCore names
 *     its polygon groups ("0".."N", the glTF material index), or every section
 *     resolves to INDEX_NONE.
 *
 * GLTFCore swaps the axes (glTF Y-up right-handed -> Unreal Z-up) but does not
 * scale: vertex positions are scaled here via SetUniformScale(100) and node
 * translations by hand.
 */
class KLIGHTSPREVIZ_API FKLightsModelLoader
{
public:
	/**
	 * @param Outer     must belong to the game world (see above) when `bCollide`.
	 * @param BaseMaterial  the opaque PBR material every glTF material instances.
	 */
	static bool Load(const FString& GlbPath, UObject* Outer, UMaterialInterface* BaseMaterial,
	                 bool bCollide, FKLightsModel& Out, FString& OutError);

	/**
	 * Components mirroring the node tree, attached under `Root`. Returns every
	 * node's component by glTF node name (pivots included), for articulation.
	 */
	static void Instantiate(const FKLightsModel& Model, AActor* Owner, USceneComponent* Root,
	                        bool bCollide, TMap<FString, USceneComponent*>& OutByName);
};
