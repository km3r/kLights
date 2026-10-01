using UnrealBuildTool;

public class KLightsPrevizEditorTarget : TargetRules
{
	public KLightsPrevizEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V7;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("KLightsPreviz");
	}
}
