import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";

import { PanelSkeleton } from "@/components/ui/skeletons";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";
import type { TranslationKey } from "@/i18n";

interface EntityRow {
  id: string;
  entity_type: string;
  canonical_name: string;
  active_assertions?: number;
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

export function EntitiesBrowsePanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [entities, setEntities] = useState<EntityRow[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiRequest<{ entities: EntityRow[] }>("/admin/memory/graph/entities?limit=100")
      .then((data) => {
        setEntities(data.entities || []);
        setError("");
      })
      .catch((requestError) => setError(String(requestError)));
  }, [refreshToken]);

  if (error)
    return (
      <div className="p-4 text-sm text-red-400">
        {t("memoryDebug.entitiesLoadError", { error })}
      </div>
    );
  if (!entities) return <PanelSkeleton />;

  return (
    <div className="space-y-4">
      <p className="font-prose text-sm text-muted-foreground">
        {t("memoryDebug.entitiesBrowseIntro")}
      </p>
      {entities.length === 0 ? (
        <Card>
          <CardContent className="font-prose p-4 text-sm text-muted-foreground">
            {t("memoryDebug.entitiesEmpty")}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-1">
          {entities.map((entity) => {
            const typeKey = ENTITY_TYPE_KEYS[entity.entity_type];
            return (
              <Link
                key={entity.id}
                to={`/admin/memory-debug/entity/${encodeURIComponent(entity.id)}`}
                className="flex items-center gap-3 rounded-md border border-border p-3 text-sm transition-colors hover:bg-muted/40"
              >
                <span className="font-medium">
                  {displayName(entity.canonical_name)}
                </span>
                <Badge variant="secondary">
                  {typeKey ? t(typeKey) : entity.entity_type}
                </Badge>
                {typeof entity.active_assertions === "number" ? (
                  <span className="text-xs text-muted-foreground">
                    {t("memory.factsCount", { count: entity.active_assertions })}
                  </span>
                ) : null}
                <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground" />
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
