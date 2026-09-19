import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Params } from "./api";
import type {
  AdjustmentOut, ArticleRef, AuditOut, BomRef, CockpitResponse, CompareResponse, LinkRef, OrderOut, ParamDoc, ParamOverrideOut,
  PdpVersionOut, PlanLine, ProductionOut, ProgramRef, ProjectionResponse, ProposalOut, ReceiptOut, ScenarioOut, SupplierRef,
} from "./types";
import { usePerimeter } from "@/state/PerimeterContext";

/** Every write invalidates the engine-derived queries (the backend cache is bumped too). */
export function useInvalidateAll() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}

export function useCockpit(extra?: Params) {
  const { engineParams, perimeter } = usePerimeter();
  const params = { planner: engineParams.planner, scenario_id: engineParams.scenario_id, horizon_days: engineParams.horizon_days, ...extra };
  return useQuery({
    queryKey: ["cockpit", params],
    queryFn: () => api.get<CockpitResponse>("/api/cockpit", params),
    enabled: perimeter.planner !== undefined,
    staleTime: 30_000,
  });
}

export function useProjection(articleId: string | undefined, extra?: Params) {
  const { engineParams, perimeter } = usePerimeter();
  const params = { granularity: perimeter.granularity, scenario_id: engineParams.scenario_id, horizon_days: engineParams.horizon_days,
    include_proposals_in_simulation: engineParams.include_proposals_in_simulation, ...extra };
  return useQuery({
    queryKey: ["projection", articleId, params],
    queryFn: () => api.get<ProjectionResponse>(`/api/articles/${encodeURIComponent(articleId!)}/projection`, params),
    enabled: !!articleId,
    staleTime: 30_000,
  });
}

export function useProposals(includeIgnored = false) {
  const { engineParams } = usePerimeter();
  const params = { planner: engineParams.planner, scenario_id: engineParams.scenario_id, include_ignored: includeIgnored };
  return useQuery({ queryKey: ["proposals", params], queryFn: () => api.get<ProposalOut[]>("/api/proposals", params), staleTime: 30_000 });
}

export const useArticles = (planner?: string | null) => useQuery({ queryKey: ["ref-articles", planner], queryFn: () => api.get<ArticleRef[]>("/api/reference/articles", { planner }), staleTime: 300_000 });
export const useSuppliers = () => useQuery({ queryKey: ["ref-suppliers"], queryFn: () => api.get<SupplierRef[]>("/api/reference/suppliers"), staleTime: 300_000 });
export const useLinks = (articleId?: string) => useQuery({ queryKey: ["ref-links", articleId], queryFn: () => api.get<LinkRef[]>("/api/reference/links", { article_id: articleId }), staleTime: 300_000 });
export const usePrograms = () => useQuery({ queryKey: ["ref-programs"], queryFn: () => api.get<ProgramRef[]>("/api/reference/programs"), staleTime: 300_000 });
export const useBom = (params?: Params) => useQuery({ queryKey: ["ref-bom", params], queryFn: () => api.get<BomRef[]>("/api/reference/bom", params), staleTime: 300_000 });
export const usePlan = (programId?: string) => useQuery({ queryKey: ["ref-plan", programId], queryFn: () => api.get<PlanLine[]>("/api/reference/plan", { program_id: programId }), staleTime: 300_000, enabled: !!programId });

export const useOrders = (params?: Params) => useQuery({ queryKey: ["orders", params], queryFn: () => api.get<OrderOut[]>("/api/entries/orders", params) });
export const useReceipts = (params?: Params) => useQuery({ queryKey: ["receipts", params], queryFn: () => api.get<ReceiptOut[]>("/api/entries/receipts", params) });
export const useAdjustments = (params?: Params) => useQuery({ queryKey: ["adjustments", params], queryFn: () => api.get<AdjustmentOut[]>("/api/entries/adjustments", params) });
export const useProduction = (params?: Params) => useQuery({ queryKey: ["production", params], queryFn: () => api.get<ProductionOut[]>("/api/entries/production", params) });
export const useScenarios = () => useQuery({ queryKey: ["scenarios"], queryFn: () => api.get<ScenarioOut[]>("/api/scenarios") });
export const useCompare = (scenarioId: string | null, planner: string | null) => useQuery({
  queryKey: ["compare", scenarioId, planner], enabled: !!scenarioId,
  queryFn: () => api.get<CompareResponse>(`/api/scenarios/${scenarioId}/compare`, { planner }),
});
export const useParamSchema = () => useQuery({ queryKey: ["param-schema"], queryFn: () => api.get<ParamDoc[]>("/api/params/schema"), staleTime: Infinity });
export const useParamEffective = () => useQuery({ queryKey: ["param-effective"], queryFn: () => api.get<Record<string, unknown>>("/api/params/effective") });
export const useOverrides = (params?: Params) => useQuery({ queryKey: ["overrides", params], queryFn: () => api.get<ParamOverrideOut[]>("/api/params/overrides", params) });
export const usePdpVersions = () => useQuery({ queryKey: ["pdp-versions"], queryFn: () => api.get<PdpVersionOut[]>("/api/pdp/versions") });
export const useAudit = (params?: Params) => useQuery({ queryKey: ["audit", params], queryFn: () => api.get<AuditOut[]>("/api/audit", params) });

/** Generic mutation that invalidates everything on success. */
export function useWrite<TIn, TOut = unknown>(fn: (input: TIn) => Promise<TOut>, onDone?: (out: TOut) => void) {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: fn, onSuccess: (out) => { invalidate(); onDone?.(out); } });
}
