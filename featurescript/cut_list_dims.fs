FeatureScript 2780;
import(path : "onshape/std/common.fs", version : "2780.0");

// Cut list dims
// -------------
// Writes each selected part's size, stock kind, grain and material into its Title 1 property, e.g.
//     762 x 590.55 x 19.05 mm sheet grain=L tag=birch
// (kind is sheet, dimensional or hardwood; older versions wrote "board", read as hardwood)
// Always millimetres, whatever the document units: it's a machine-readable value, and the
// optimizer displays it in inches, feet or mm as you choose.
// The plywood optimizer reads that string from an assembly BOM CSV export.
//
// Setup (once): create a Feature Studio, keep the two lines Onshape generates at the top
// (they pin the current FeatureScript version), and paste everything below them.
// Use: add "Cut list dims" at the end of a Part Studio and select parts. Each instance applies one
// set of options (stock, grain, material) to any number of parts of any thickness; add another instance
// for parts that need different options (e.g. boards, or grain-locked parts).
//
// Thickness is measured perpendicular to the part's largest flat face; length and width are
// measured along that face, aligned to its longest straight edge, so tilted parts measure true.
//
// Material: leave it blank to use each part's own Onshape material (the optimizer also reads the
// BOM's Material column when Title 1 has none); type one in to override it for these parts.

export enum CutListKind
{
    annotation { "Name" : "Sheet" }
    SHEET,
    annotation { "Name" : "Dimensional (2x4, 1x6)" }
    DIMENSIONAL,
    // Still BOARD inside, so features made before dimensional lumber existed stay hardwood.
    annotation { "Name" : "Hardwood" }
    BOARD
}

export enum CutListGrain
{
    annotation { "Name" : "No preference" }
    NONE,
    annotation { "Name" : "Along length" }
    LENGTH,
    annotation { "Name" : "Along width" }
    WIDTH
}

annotation { "Feature Type Name" : "Cut list dims",
        "Feature Type Description" : "Writes length x width x thickness, stock kind, grain and material into each part's Title 1 property for the plywood cut list optimizer." }
export const cutListDims = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Parts", "Filter" : EntityType.BODY && BodyType.SOLID }
        definition.parts is Query;

        annotation { "Name" : "Stock" }
        definition.kind is CutListKind;

        annotation { "Name" : "Grain", "UIHint" : UIHint.SHOW_LABEL }
        definition.grain is CutListGrain;

        // Still called "tag" inside, so features made before the rename keep their text.
        annotation { "Name" : "Material", "Description" : "Blank: each part's own Onshape material" }
        definition.tag is string;
    }
    {
        const bodies = evaluateQuery(context, definition.parts);
        if (size(bodies) == 0)
        {
            throw regenError("Select at least one part", ["parts"]);
        }

        for (var body in bodies)
        {
            const dims = cutListMeasure(context, body);
            var text = cutListNumber(dims[0] / millimeter) ~ " x " ~ cutListNumber(dims[1] / millimeter) ~ " x " ~
                cutListNumber(dims[2] / millimeter) ~ " mm";
            if (definition.kind == CutListKind.BOARD)
            {
                text = text ~ " hardwood";
            }
            else if (definition.kind == CutListKind.DIMENSIONAL)
            {
                text = text ~ " dimensional";
            }
            else
            {
                text = text ~ " sheet";
            }
            if (definition.grain == CutListGrain.LENGTH)
            {
                text = text ~ " grain=L";
            }
            else if (definition.grain == CutListGrain.WIDTH)
            {
                text = text ~ " grain=W";
            }
            const material = definition.tag != "" ? definition.tag : cutListMaterial(context, body);
            if (material != "")
            {
                text = text ~ " tag=" ~ material;
            }

            setProperty(context, {
                    "entities" : body,
                    "propertyType" : PropertyType.TITLE_1,
                    "value" : text
            });
        }
    });

/** The part's Onshape material name, or "" if it has none (or it can't be read here). */
function cutListMaterial(context is Context, body is Query) returns string
{
    var name = "";
    try silent
    {
        const material = getProperty(context, { "entity" : body, "propertyType" : PropertyType.MATERIAL });
        if (material != undefined && material.name != undefined)
        {
            name = material.name;
        }
    }
    return name;
}

function cutListNumber(value is number) returns string
{
    return toString(roundToPrecision(value, 3));
}

/** Returns [length, width, thickness] with length >= width. */
function cutListMeasure(context is Context, body is Query) returns array
{
    const planarFaces = evaluateQuery(context, qGeometry(qOwnedByBody(body, EntityType.FACE), GeometryType.PLANE));
    if (size(planarFaces) == 0)
    {
        // No flat faces: fall back to a world-aligned box, sorted largest to smallest.
        const worldBounds = evBox3d(context, { "topology" : body, "tight" : true });
        const worldExtent = worldBounds.maxCorner - worldBounds.minCorner;
        const x = abs(worldExtent[0]);
        const y = abs(worldExtent[1]);
        const z = abs(worldExtent[2]);
        const longest = max(max(x, y), z);
        const shortest = min(min(x, y), z);
        return [longest, x + y + z - longest - shortest, shortest];
    }

    var bestFace = planarFaces[0];
    var bestArea = evArea(context, { "entities" : bestFace });
    for (var candidate in planarFaces)
    {
        const candidateArea = evArea(context, { "entities" : candidate });
        if (candidateArea > bestArea)
        {
            bestArea = candidateArea;
            bestFace = candidate;
        }
    }
    const facePlane = evPlane(context, { "face" : bestFace });

    // Align x with the face's longest straight edge so an in-plane rotation doesn't inflate the box.
    var xDir = facePlane.x;
    var bestLength = 0 * meter;
    const straightEdges = evaluateQuery(context, qGeometry(qAdjacent(bestFace, AdjacencyType.EDGE, EntityType.EDGE), GeometryType.LINE));
    for (var straightEdge in straightEdges)
    {
        const edgeLength = evLength(context, { "entities" : straightEdge });
        if (edgeLength > bestLength)
        {
            bestLength = edgeLength;
            xDir = evLine(context, { "edge" : straightEdge }).direction;
        }
    }
    xDir = normalize(xDir - dot(xDir, facePlane.normal) * facePlane.normal);

    const measureCSys = coordSystem(facePlane.origin, xDir, facePlane.normal);
    const bounds = evBox3d(context, { "topology" : body, "cSys" : measureCSys, "tight" : true });
    const extent = bounds.maxCorner - bounds.minCorner;
    const alongX = abs(extent[0]);
    const alongY = abs(extent[1]);
    return [max(alongX, alongY), min(alongX, alongY), abs(extent[2])];
}
