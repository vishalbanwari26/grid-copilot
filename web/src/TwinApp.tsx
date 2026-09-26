import { useEffect, useState } from "react";
import GridDashboard from "./components/twin/GridDashboard";
import SubstationDashboard from "./components/twin/SubstationDashboard";
import TransformerDashboard from "./components/twin/TransformerDashboard";
import ComponentDashboard from "./components/twin/ComponentDashboard";

type Place =
  | { level: "grid" }
  | { level: "substation"; substation: string }
  | { level: "transformer"; substation: string; asset: string }
  | { level: "component"; substation: string; asset: string; component: string };

// The substation a transformer belongs to comes from the twin; this map lets a
// deep link to a transformer rebuild its breadcrumb without an extra request.
const SUBSTATION_OF: Record<string, string> = {
  "TR-01": "Nordhafen", "TR-02": "Nordhafen", "TR-03": "Altmühl", "TR-04": "Altmühl", "TR-05": "Sandberg",
  "TR-06": "Sandberg", "TR-07": "Kreuzweg", "TR-08": "Kreuzweg", "TR-09": "Lindenau",
};

const COMPONENT_LABEL: Record<string, string> = {
  active_part: "Active part", oltc: "Tap changer", cooling: "Cooling", dga_monitor: "DGA monitor",
  top_oil_pt100: "Top-oil sensor", oltc_temp: "Tap-changer temperature", pd_uhf: "PD sensor",
  load_measurement: "Load measurement",
};

function fromUrl(): Place {
  const p = new URLSearchParams(window.location.search);
  const asset = p.get("asset");
  const component = p.get("component");
  const substation = p.get("substation") ?? (asset ? SUBSTATION_OF[asset] : null);
  if (asset && component && substation) return { level: "component", substation, asset, component };
  if (asset && substation) return { level: "transformer", substation, asset };
  if (substation) return { level: "substation", substation };
  return { level: "grid" };
}

function toUrl(place: Place) {
  const p = new URLSearchParams(window.location.search);
  for (const k of ["substation", "asset", "component"]) p.delete(k);
  if (place.level !== "grid") p.set("substation", place.substation);
  if (place.level === "transformer" || place.level === "component") p.set("asset", place.asset);
  if (place.level === "component") p.set("component", place.component);
  const q = p.toString();
  window.history.pushState(null, "", q ? `?${q}` : window.location.pathname);
}

export default function TwinApp() {
  const [place, setPlace] = useState<Place>(fromUrl);
  useEffect(() => {
    const onPop = () => setPlace(fromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = (p: Place) => {
    setPlace(p);
    toUrl(p);
    window.scrollTo({ top: 0 });
  };

  const crumbs: { label: string; place: Place }[] = [{ label: "Grid", place: { level: "grid" } }];
  if (place.level !== "grid") crumbs.push({ label: place.substation, place: { level: "substation", substation: place.substation } });
  if (place.level === "transformer" || place.level === "component")
    crumbs.push({ label: place.asset, place: { level: "transformer", substation: place.substation, asset: place.asset } });
  if (place.level === "component")
    crumbs.push({ label: COMPONENT_LABEL[place.component] ?? place.component.replace(/_/g, " "), place });

  return (
    <>
      <nav className="crumbs">
        {crumbs.map((c, i) => (
          <span key={i}>
            {i > 0 && <span className="sep">/</span>}
            {i === crumbs.length - 1 ? (
              <span className="here">{c.label}</span>
            ) : (
              <button onClick={() => go(c.place)}>{c.label}</button>
            )}
          </span>
        ))}
      </nav>
      {place.level === "grid" && (
        <GridDashboard
          onSubstation={(s) => go({ level: "substation", substation: s })}
          onTransformer={(a) => go({ level: "transformer", substation: SUBSTATION_OF[a], asset: a })}
        />
      )}
      {place.level === "substation" && (
        <SubstationDashboard
          id={place.substation}
          onTransformer={(a) => go({ level: "transformer", substation: place.substation, asset: a })}
        />
      )}
      {place.level === "transformer" && (
        <TransformerDashboard
          id={place.asset}
          onComponent={(c) => go({ level: "component", substation: place.substation, asset: place.asset, component: c })}
        />
      )}
      {place.level === "component" && <ComponentDashboard asset={place.asset} id={place.component} />}
      <div className="foot">
        Fictional network on real ETT load profiles · tools do the arithmetic, the model weighs the evidence, a named
        engineer decides
      </div>
    </>
  );
}
