using Uex.Core;
using Xunit;

namespace Uex.Tests;

public class AssetOpsTests
{
    [Theory]
    [InlineData("staticmesh", "C:/out/mesh.glb", "meshexport: staticmesh -> C:/out/mesh.glb")]
    [InlineData("skeletalmesh", "out.glb", "meshexport: skeletalmesh -> out.glb")]
    public void MeshExportLine_formats_kind(string kind, string path, string expected) =>
        Assert.Equal(expected, AssetOps.MeshExportLine(kind, path));

    [Fact]
    public void MeshExportLine_reports_none_when_no_mesh()
    {
        Assert.Equal("meshexport: none", AssetOps.MeshExportLine(null, "C:/out/mesh.glb"));
        Assert.Equal("meshexport: none", AssetOps.MeshExportLine(null, ""));
    }

    [Fact]
    public void Mesh_kinds_are_the_glb_export_contract()
    {
        // DualForge's parse_mesh_summary matches on these exact values.
        Assert.Equal("staticmesh", AssetOps.StaticMeshKind);
        Assert.Equal("skeletalmesh", AssetOps.SkeletalMeshKind);
    }
}