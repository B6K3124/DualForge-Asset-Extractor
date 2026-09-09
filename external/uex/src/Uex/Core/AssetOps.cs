using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Textures;
using Newtonsoft.Json;
using SharpGLTF.Memory;
using SharpGLTF.Schema2;

namespace Uex.Core;

/// <summary>Single-asset operations shared by CLI, serve mode and MCP.</summary>
public static class AssetOps
{
    /// <summary>Resolve a user path to an exact Files key: exact, then +.uasset/.umap; on failure suggest near matches by file name.</summary>
    public static string ResolvePackagePath(DefaultFileProvider provider, string input)
    {
        var path = OutputPaths.Normalize(input);
        foreach (var candidate in new[] { path, path + ".uasset", path + ".umap" })
            if (provider.Files.ContainsKey(candidate))
                return candidate;
        var name = path[(path.LastIndexOf('/') + 1)..];
        var suggestions = VfsQuery.Search(provider.Files.Keys, name, regex: false, limit: 5);
        var hint = suggestions.Matches.Count > 0
            ? $" Did you mean:\n  {string.Join("\n  ", suggestions.Matches)}"
            : "";
        throw new UexException($"Asset not found: {input}.{hint}");
    }

    /// <summary>FModel-compatible package JSON: the serialized array of exports.</summary>
    public static string SerializePackage(DefaultFileProvider provider, string vpath)
    {
        var package = provider.LoadPackage(vpath);
        return JsonConvert.SerializeObject(package.GetExports(), Formatting.Indented);
    }

    /// <summary>Decode the first texture export of a package to a PNG file; returns the written path.</summary>
    public static string SavePng(DefaultFileProvider provider, string vpath, string outPath)
    {
        var package = provider.LoadPackage(vpath);
        foreach (var export in package.GetExports())
        {
            if (export is not UTexture texture) continue;
            var decoded = texture.Decode()
                ?? throw new UexException($"Texture failed to decode: {vpath}");
            var png = decoded.Encode(ETextureFormat.Png, false, out _);
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath))!);
            File.WriteAllBytes(outPath, png);
            return Path.GetFullPath(outPath);
        }
        throw new UexException($"No texture export in package: {vpath}");
    }

    /// <summary>Serialize with a byte cap for agent previews; truncated output is marked and not valid JSON.</summary>
    public static string Preview(DefaultFileProvider provider, string vpath, int maxBytes)
    {
        string content;
        if (Aion2Dat.Handles(provider.Versions.Game, vpath))
            content = Aion2Dat.ToJson(provider, vpath);
        else if (OutputPaths.IsPackage(vpath))
            content = SerializePackage(provider, vpath);
        else
            throw new UexException($"Not a previewable asset (UE package or known data file): {vpath}");

        if (content.Length <= maxBytes) return content;
        return content[..maxBytes] + $"\n... [truncated {content.Length - maxBytes} of {content.Length} chars - use --max-bytes to raise]";
    }

    public const string StaticMeshKind = "staticmesh";
    public const string SkeletalMeshKind = "skeletalmesh";

    /// <summary>Export the first mesh export of a package to a GLB file; returns the mesh kind or null if none found.</summary>
    /// <param name="withMaterials">Embed base-color textures in the GLB (true) or export untextured geometry only (false).</param>
    public static string? SaveMeshGLB(DefaultFileProvider provider, string vpath, string outPath, bool withMaterials = false)
    {
        var package = provider.LoadPackage(vpath);
        var export = package.GetExports().FirstOrDefault(e => e is UStaticMesh or USkeletalMesh);
        if (export is null) return null;

        // The exporter pipeline writes under a session output dir keyed by the package path, so run it
        // into a temp dir and copy the produced .glb to the requested outPath.
        var tempDir = Path.Combine(Path.GetTempPath(), "uex-mesh", Path.GetRandomFileName());
        Directory.CreateDirectory(tempDir);
        try
        {
            var session = new ExportSession();
            session.Add(export);
            var options = new ExportOptions(
                meshFormat: EMeshFormat.Gltf2,
                exportMaterials: withMaterials,
                exportMorphTargets: false);
            var result = session.RunAsync(tempDir, options).GetAwaiter().GetResult()
                .FirstOrDefault(r => r.Success);
            var glb = result?.DiskFilePaths?.FirstOrDefault(p => p.EndsWith(".glb", StringComparison.OrdinalIgnoreCase));
            if (glb is null) return null;

            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath))!);
            File.Copy(glb, outPath, overwrite: true);
            if (withMaterials)
            {
                EmbedBaseColorTextures(export, outPath);
            }
            return export is UStaticMesh ? StaticMeshKind : SkeletalMeshKind;
        }
        finally
        {
            Directory.Delete(tempDir, recursive: true);
        }
    }

    /// <summary>Best-effort base-color texture bake for a mesh GLB, material-slot aware.</summary>
    private static void EmbedBaseColorTextures(UObject export, string glbPath)
    {
        var slots = new List<FPackageIndex?>();
        switch (export)
        {
            case USkeletalMesh skeletal:
                foreach (var material in skeletal.SkeletalMaterials)
                    slots.Add(material.Material);
                break;
            case UStaticMesh staticMesh:
                foreach (var material in staticMesh.StaticMaterials)
                    slots.Add(material.MaterialInterface);
                break;
        }

        var pngByName = new Dictionary<string, byte[]>(StringComparer.Ordinal);
        foreach (var index in slots)
        {
            if (index is not { } materialIndex) continue;
            if (!materialIndex.TryLoad<UMaterialInterface>(out var material) || material is null) continue;
            if (string.IsNullOrEmpty(material.Name) || pngByName.ContainsKey(material.Name)) continue;

            var parameters = new CMaterialParams2();
            material.GetParams(parameters, EMaterialDepth.AllLayers);
            var texture = parameters.TryGetTexture2d(out var diffuse, CMaterialParams2.FallbackDiffuse)
                ? diffuse
                : parameters.Textures.Values.OfType<UTexture>().FirstOrDefault();
            if (texture is null) continue;

            var decoded = texture.Decode();
            if (decoded is null) continue;
            pngByName[material.Name] = decoded.Encode(ETextureFormat.Png, false, out _);
        }

        if (pngByName.Count == 0) return;

        var model = ModelRoot.ParseGLB(File.ReadAllBytes(glbPath));
        foreach (var material in model.LogicalMaterials)
        {
            if (!pngByName.TryGetValue(material.Name, out var png)) continue;
            var image = model.UseImage(new MemoryImage(png));
            var texture = model.UseTexture(image, null);
            material.FindChannel("BaseColor")?.SetTexture(0, texture);
        }
        model.SaveGLB(glbPath);
    }

    /// <summary>One-line status for the CLI/MCP surfaces, also the contract DualForge parses ("meshexport: ...").</summary>
    public static string MeshExportLine(string? kind, string outPath) =>
        kind is null ? "meshexport: none" : $"meshexport: {kind} -> {outPath}";
}
