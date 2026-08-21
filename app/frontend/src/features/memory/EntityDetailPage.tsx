import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AssertionCard } from "@/features/memory/components/AssertionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { PanelSkeleton } from "@/components/ui/skeletons";
import { apiRequest, normalizeApiError } from "@/services/bimemo-api";
import { QUERY_KEYS, errorToDisplayText } from "@/hooks/use-server-state";
import type { TranslationKey } from "@/i18n";
import { cn } from "@/utils/utils";

interface EntityRow {
  id: string;
  entity_type: string;
  canonical_name: string;
  aliases: string[];
  created_at: string;
}

interface AssertionRow {
  id: string;
  subject_entity_id: string;
  relation: string;
  object_entity_id: string | null;
  object_value: string | null;
  assertion_text: string;
  trust_level: string;
  valid_from: string | null;
  invalid_from: string | null;
  created_at: string;
  direction?: "subject" | "object";
}

interface RelatedEntity {
  id: string;
  canonical_name: string;
  entity_type: string;
}

interface EntityDetail {
  entity: EntityRow;
  assertions: AssertionRow[];
  related: Record<string, RelatedEntity>;
}

const ENTITY_TYPE_KEYS: Record<string, TranslationKey> = {
  person: "memory.typePerson",
  tool: "memory.typeTool",
  topic: "memory.typeTopic",
  project: "memory.typeProject",
  place: "memory.typePlace",
  organization: "memory.typeOrganization",
  unresolved: "memory.typeUnresolved",
};

function displayName(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function EntityDetailPage() {
  const { t } = useTranslation();
  const { entityId } = useParams<{ entityId: string }>();

  // The query is keyed by the route id, so a result for a previous id
  // (mid-navigation) never leaks in: a new id starts as a fresh pending
  // query and the page shows the skeleton until it settles.
  const { data, error: queryError } = useQuery({
    queryKey: QUERY_KEYS.entityDetail(entityId ?? ""),
    queryFn: () =>
      apiRequest<EntityDetail>(
        `/admin/memory/graph/entities/${encodeURIComponent(entityId ?? "")}`
      ),
    enabled: !!entityId,
  });

  // A 404 means the entity is gone: show the not-found view, not an error.
  // Every other failure goes through the closed error mapping. The derived
  // locals keep the LoadState shape (detail / notFound / error), so the
  // render tree below is unchanged.
  const normalized = queryError ? normalizeApiError(queryError) : null;
  const notFound =
    normalized !== null && normalized.kind === "http" && normalized.status === 404;
  const detail = notFound ? null : (data ?? null);
  const error =
    normalized !== null && !notFound ? errorToDisplayText(queryError, t) : "";

  const typeLabel = (type: string): string => {
    const key = ENTITY_TYPE_KEYS[type];
    return key ? t(key) : type;
  };

  return (
    <AdminLayout title={t("memoryDebug.entityPageTitle")}>
      <div className="space-y-4 p-4 sm:p-6">
        <Button asChild variant="ghost" size="sm">
          <Link to="/admin/memory-debug">
            <ArrowLeft className="h-4 w-4" />
            {t("memoryDebug.backToDebug")}
          </Link>
        </Button>

        {error ? (
          <div className="p-4 text-sm text-red-400">
            {t("memoryDebug.entityLoadError", { error })}
          </div>
        ) : notFound ? (
          <Empty>
            <EmptyHeader>
              <EmptyTitle>{t("memoryDebug.entityNotFound")}</EmptyTitle>
            </EmptyHeader>
          </Empty>
        ) : !detail ? (
          <PanelSkeleton />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-semibold tracking-tight">
                {displayName(detail.entity.canonical_name)}
              </h2>
              <Badge variant="secondary">{typeLabel(detail.entity.entity_type)}</Badge>
              {detail.entity.aliases.length > 0 ? (
                <span className="font-prose text-xs text-muted-foreground">
                  {t("memoryDebug.entityAliases", {
                    value: detail.entity.aliases.join(", "),
                  })}
                </span>
              ) : null}
            </div>

            {detail.assertions.length === 0 ? (
              <Card>
                <CardContent className="font-prose p-4 text-sm text-muted-foreground">
                  {t("memoryDebug.entityNoAssertions")}
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-2">
                {detail.assertions.map((assertion) => {
                  const neighborId =
                    assertion.direction === "subject"
                      ? assertion.object_entity_id
                      : assertion.subject_entity_id;
                  const neighbor = neighborId ? detail.related[neighborId] : undefined;
                  return (
                    <Card
                      key={assertion.id}
                      className={cn(assertion.invalid_from && "opacity-60")}
                    >
                      <CardContent className="space-y-2 p-3">
                        <AssertionCard
                          data={{
                            id: assertion.id,
                            text: assertion.assertion_text,
                            relation: assertion.relation,
                            trustLevel: assertion.trust_level,
                            validFrom: assertion.valid_from,
                            invalidFrom: assertion.invalid_from,
                            createdAt: assertion.created_at,
                          }}
                        />
                        {neighbor ? (
                          <Button asChild variant="ghost" size="sm">
                            <Link
                              to={`/admin/memory-debug/entity/${encodeURIComponent(neighbor.id)}`}
                            >
                              {t("memoryDebug.entityNeighborLink", {
                                name: displayName(neighbor.canonical_name),
                                type: typeLabel(neighbor.entity_type),
                              })}
                            </Link>
                          </Button>
                        ) : null}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </AdminLayout>
  );
}
