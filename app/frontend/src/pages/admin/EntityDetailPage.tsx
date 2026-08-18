import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AssertionCard } from "@/components/admin/memory-debug/AssertionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { PanelSkeleton } from "@/components/ui/skeletons";
import { ApiError, apiRequest } from "@/lib/bimemo-api";
import type { TranslationKey } from "@/i18n";
import { cn } from "@/lib/utils";

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

interface LoadState {
  entityId: string;
  detail: EntityDetail | null;
  notFound: boolean;
  error: string;
}

export function EntityDetailPage() {
  const { t } = useTranslation();
  const { entityId } = useParams<{ entityId: string }>();
  const [state, setState] = useState<LoadState | null>(null);

  useEffect(() => {
    if (!entityId) return undefined;
    let cancelled = false;
    const settle = (partial: Omit<LoadState, "entityId">): void => {
      if (!cancelled) setState({ entityId, ...partial });
    };
    apiRequest<EntityDetail>(
      `/admin/memory/graph/entities/${encodeURIComponent(entityId)}`
    )
      .then((detail) => settle({ detail, notFound: false, error: "" }))
      .catch((requestError) => {
        if (requestError instanceof ApiError && requestError.status === 404) {
          settle({ detail: null, notFound: true, error: "" });
        } else {
          settle({ detail: null, notFound: false, error: String(requestError) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [entityId]);

  // Treat a result for a previous id (mid-navigation) as still loading.
  const current = state && state.entityId === entityId ? state : null;
  const detail = current?.detail ?? null;
  const notFound = current?.notFound ?? false;
  const error = current?.error ?? "";

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
