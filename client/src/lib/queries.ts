import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Params } from "./api";
import type {
  AdjustmentOut, ArticleRef, AuditOut, BacklogRow, CockpitResponse, DeliveryPlanResponse, FlagOut, GridResponse, LinkRef, ParamDoc, ParamOverrideOut, PdpErpLine,
  PdpVersionOut, PlanCellOut, ProgramImpactResponse, ProgramRef, ProjectionResponse, ProposalOut, RefRow, RefTableInfo, WeeklyParamsResponse,
} from "./types";
import { usePerimeter } from "@/state/PerimeterContext";
import { useToast } from "@/components/ui";

/** Every write invalidates the engine-derived queries (the backend cache is bumped too). */
export function useInvalidateAll() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}

export function useCockpit(extra?: Params) {
  const { engineParams, perimeter } = usePerimeter();
  const params = { ...engineParams, ...extra };
  return useQuery({ queryKey: ["cockpit", params], queryFn: () => api.get<CockpitResponse>("/api/cockpit", params), enabled: perimeter.planner !== undefined, staleTime: 30_000 });
}

export function useProjection(articleId: string | undefined, extra?: Params) {
  const { engineParams, perimeter } = usePerimeter();
  const params = { granularity: perimeter.granularity, horizon_days: engineParams.horizon_days, ...extra };
  return useQuery({
    queryKey: ["projection", articleId, params],
    queryFn: () => api.get<ProjectionResponse>(`/api/articles/${encodeURIComponent(articleId!)}/projection`, params),
    enabled: !!articleId, staleTime: 30_000,
  });
}

export function useProposals() {
  const { engineParams } = usePerimeter();
  const params = { planner: engineParams.planner };
  return useQuery({ queryKey: ["proposals", params], queryFn: () => api.get<ProposalOut[]>("/api/proposals", params), staleTime: 30_000 });
}

export function useBacklog() {
  const { engineParams } = usePerimeter();
  const params = { planner: engineParams.planner };
  return useQuery({ queryKey: ["backlog", params], queryFn: () => api.get<BacklogRow[]>("/api/backlog", params), staleTime: 30_000 });
}

/** One page of the multi-article supply table (same columns for every article) ; the previous page
 * stays on screen while the next one loads. */
export function useGrid(extra?: Params) {
  const { engineParams, perimeter } = usePerimeter();
  const params = { ...engineParams, granularity: perimeter.granularity, ...extra };
  return useQuery({ queryKey: ["grid", params], queryFn: () => api.get<GridResponse>("/api/grid", params), staleTime: 30_000, placeholderData: keepPreviousData });
}

export function useDeliveryPlan(articleId: string | undefined) {
  const { engineParams } = usePerimeter();
  const params = { horizon_days: engineParams.horizon_days };
  return useQuery({ queryKey: ["delivery-plan", articleId, params], enabled: !!articleId, staleTime: 30_000,
    queryFn: () => api.get<DeliveryPlanResponse>(`/api/articles/${encodeURIComponent(articleId!)}/delivery-plan`, params) });
}

export function useProgramImpact() {
  const { engineParams } = usePerimeter();
  return useQuery({ queryKey: ["program-impact", engineParams], queryFn: () => api.get<ProgramImpactResponse>("/api/programs/impact", engineParams), staleTime: 30_000 });
}

/** The two editable rows: typed plan cells and adjustment cells. */
export const usePlanCells = (params?: Params) => useQuery({ queryKey: ["plan-cells", params], queryFn: () => api.get<PlanCellOut[]>("/api/entries/plan", params) });
export const useAdjustments = (params?: Params) => useQuery({ queryKey: ["adjustments", params], queryFn: () => api.get<AdjustmentOut[]>("/api/entries/adjustments", params) });
/** Clicks on read-only cells: ignored firm-order days, refused CBN proposals. */
export const useFlags = (params?: Params) => useQuery({ queryKey: ["flags", params], queryFn: () => api.get<FlagOut[]>("/api/entries/flags", params) });
/** Weekly stock-policy calendar of an article: from the reference week to the last PDP week / end of horizon. */
export const useWeeklyParams = (articleId: string | null) => useQuery({
  queryKey: ["weekly-params", articleId], enabled: !!articleId,
  queryFn: () => api.get<WeeklyParamsResponse>(`/api/articles/${encodeURIComponent(articleId!)}/weekly-params`),
});

export const useArticles = (planner?: string | null) => useQuery({ queryKey: ["ref-articles", planner], queryFn: () => api.get<ArticleRef[]>("/api/reference/articles", { planner }), staleTime: 300_000 });
export const useLinks = (articleId?: string) => useQuery({ queryKey: ["ref-links", articleId], queryFn: () => api.get<LinkRef[]>("/api/reference/links", { article_id: articleId }), staleTime: 300_000 });
export const usePrograms = () => useQuery({ queryKey: ["ref-programs"], queryFn: () => api.get<ProgramRef[]>("/api/reference/programs"), staleTime: 300_000 });
export const usePdpErp = (programId?: string) => useQuery({ queryKey: ["ref-pdp", programId], queryFn: () => api.get<PdpErpLine[]>("/api/reference/pdp", { program_id: programId }), staleTime: 300_000, enabled: !!programId });
export const useRefTables = () => useQuery({ queryKey: ["ref-tables"], queryFn: () => api.get<RefTableInfo[]>("/api/reference/tables") });
export const useRefRows = (name: string | null) => useQuery({ queryKey: ["ref-rows", name], queryFn: () => api.get<RefRow[]>(`/api/reference/${name}/rows`), enabled: !!name });

export const useParamSchema = () => useQuery({ queryKey: ["param-schema"], queryFn: () => api.get<ParamDoc[]>("/api/params/schema"), staleTime: Infinity });
export const useParamEffective = () => useQuery({ queryKey: ["param-effective"], queryFn: () => api.get<Record<string, unknown>>("/api/params/effective") });
export const useOverrides = (params?: Params) => useQuery({ queryKey: ["overrides", params], queryFn: () => api.get<ParamOverrideOut[]>("/api/params/overrides", params) });
export const usePdpVersions = () => useQuery({ queryKey: ["pdp-versions"], queryFn: () => api.get<PdpVersionOut[]>("/api/pdp/versions") });
export const useAudit = (params?: Params) => useQuery({ queryKey: ["audit", params], queryFn: () => api.get<AuditOut[]>("/api/entries/audit", params) });

/** Generic mutation that invalidates everything on success and shows the server message on failure
 * (a refused write – 403 outside the user's portfolio, 422 invalid value – is never silent). */
export function useWrite<TIn, TOut = unknown>(fn: (input: TIn) => Promise<TOut>, onDone?: (out: TOut) => void) {
  const invalidate = useInvalidateAll();
  const toast = useToast();
  return useMutation({ mutationFn: fn, onSuccess: (out) => { invalidate(); onDone?.(out); }, onError: (e) => toast.push((e as Error).message, "error") });
}
