import { beforeEach, describe, expect, it } from "vitest";
import { Charity } from "../models.js";
import { IDS, buildGatesGraph } from "./gatesGraph.js";

describe("BrowseViewModel", () => {
  let g;
  beforeEach(() => {
    g = buildGatesGraph();
  });

  it("tunnelNode leaves only that org on the show list", () => {
    g.nodes.trust.place(1, 1);
    g.nodes.foundation.place(1, 1);
    g.nodes.trust.tunnelNode();
    const show = g.vm.getShowList();
    expect(show).toHaveLength(1);
    expect(show[0].startsWith(IDS.trust)).toBe(true);
    expect(g.nodes.trust.desiredVisible).toBe(true);
    expect(g.nodes.foundation.desiredVisible).toBe(false);
  });

  it("loadPreset add unions, replace replaces", () => {
    g.vm.loadPreset({ eins: [IDS.trust] }, "add");
    g.vm.loadPreset({ eins: [IDS.foundation] }, "add");
    const added = g.vm.getShowList().map((s) => s.split("~")[0]);
    expect(added).toEqual(expect.arrayContaining([IDS.trust, IDS.foundation]));
    g.vm.loadPreset({ eins: [IDS.mit] }, "replace");
    const replaced = g.vm.getShowList().map((s) => s.split("~")[0]);
    expect(replaced).toEqual([IDS.mit]);
  });

  it("URL round-trips e= seeds", () => {
    g.vm.setShowList([`${IDS.trust}~1~1`]);
    const params = g.vm.computeURLParams();
    expect(params.getAll("e").some((e) => e.startsWith(IDS.trust))).toBe(true);
    g.nodes.trust.clearVisibility();
    g.vm.parseQueryParams(params);
    expect(g.vm.getShowList().some((e) => e.startsWith(IDS.trust))).toBe(true);
  });

  it("legacy ein= is read by parseParamsWithOldNew", () => {
    const params = new URLSearchParams();
    params.append("ein", `${IDS.trust}~2~2`);
    g.vm.parseQueryParams(params);
    const show = g.vm.getShowList();
    expect(show.some((e) => e.startsWith(IDS.trust))).toBe(true);
  });

  it("hide list drops the node from sankey data", () => {
    g.nodes.trust.place(1, 1);
    g.vm.computeImpliedVisibility();
    g.vm.addToHideList(IDS.mit);
    const data = g.vm.buildSankeyData();
    const ids = data.nodes.map((n) => n.ein);
    expect(ids).not.toContain(IDS.mit);
  });

  it("implied hop from Trust does not seed the Foundation 990 as a desired node", () => {
    g.nodes.trust.place(0, 1);
    g.vm.computeImpliedVisibility();
    expect(g.nodes.trust.desiredVisible).toBe(true);
    expect(g.nodes.foundation.desiredVisible).toBe(false);
    const ghostVisible =
      g.nodes.ghost.isVisible || g.nodes.ghost.impliedVisible > 0;
    expect(ghostVisible).toBe(true);
  });

  it("leftover stub is not a 990 card", () => {
    g.nodes.trust.place(0, 3);
    expect(g.nodes.leftover.has990Card).toBe(false);
    expect(g.nodes.leftover.kind).toBe("leftover");
  });

  it("buildSankeyData node ids are graph keys; longEIN empty on ghosts", () => {
    g.nodes.trust.place(1, 3);
    g.vm.computeImpliedVisibility();
    const data = g.vm.buildSankeyData();
    for (const n of data.nodes) {
      expect(n.id).toBeTruthy();
      if (n.isGhost || n.isLeftover) {
        expect(n.longEIN).toBe("");
      }
    }
  });
});
