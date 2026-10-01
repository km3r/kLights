using UnrealBuildTool;

public class KLightsPreviz : ModuleRules
{
	public KLightsPreviz(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] {
			"Core", "CoreUObject", "Engine", "InputCore",
		});

		PrivateDependencyModuleNames.AddRange(new string[] {
			// Art-Net in, scene and models from the engine over HTTP.
			"Sockets", "Networking", "HTTP", "Json",
			// Runtime glTF: GLTFCore parses, the mesh is built from a
			// MeshDescription at runtime -- the recipe Interchange itself uses
			// in a cooked game (InterchangeStaticMeshFactory.cpp:936).
			"GLTFCore", "MeshDescription", "StaticMeshDescription",
			"RenderCore",
		});
	}
}
