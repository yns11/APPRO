import type { SVGProps } from "react";
import { ArrowLeftRight, Truck } from "lucide-react";
import type { TransportOut } from "@/lib/types";

/** A pallet : the EV return (the supplier takes his empty packaging back). */
export function PalletIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <rect x="3" y="15" width="18" height="4" rx="1" /><path d="M7 15v4M12 15v4M17 15v4" /><rect x="5" y="5" width="6" height="10" rx="1" /><rect x="13" y="9" width="6" height="6" rx="1" />
    </svg>
  );
}

/** The icon of a truck without supplier orders : arrows for the MAF shuttle, a pallet for the EV return, a truck for the one-off truck ; nothing for a supplier truck. */
export function KindIcon({ t, size = 12 }: { t: Pick<TransportOut, "mode">; size?: number }) {
  if (t.mode === "maf") return <ArrowLeftRight size={size} aria-label="navette MAF" />;
  if (t.mode === "ev") return <PalletIcon width={size} height={size} aria-label="retour EV" />;
  if (t.mode === "ponctuel") return <Truck size={size} aria-label="camion ponctuel" />;
  return null;
}
