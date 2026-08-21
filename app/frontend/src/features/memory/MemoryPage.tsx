import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Brain, ExternalLink, Search } from "lucide-react";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { ReauthModal } from "@/features/auth/components/ReauthModal";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ToastContainer, useToasts } from "@/components/ui/Toast";
import {
  getMemoryEntity,
  invalidateMemoryAssertion,
  listMemoryEntities,
  resolveMemoryRecord,
  searchMemoryEntities,
  type InvalidateReason,
  type MemoryAssertion,
  type MemoryEntity,
  type MemoryRecordRef,
} from "@/features/memory/api/memory";
import { queryClient } from "@/services/query-client";
import { QUERY_KEYS, errorToDisplayText } from "@/hooks/use-server-state";
import { formatAbsoluteTime } from "@/features/scheduler/model/scheduler-format";
import type { TranslationKey } from "@/i18n";
import { cn } from "@/utils/utils";

const ENTITY_TYPE_KEYS: Record<string, TranslationKey> = {
  person: "memory.typePerson",
  tool: "memory.typeTool",
  topic: "memory.typeTopic",
  project: "memory.typeProject",
  place: "memory.typePlace",
  organization: "memory.typeOrganization",
  unresolved: "memory.typeUnresolved",
};

const TRUST_KEYS: Record<string, TranslationKey> = {
  high: "memory.trustHigh",
  medium: "memory.trustMedium",
  low: "memory.trustLow",
};

const TRUST_BADGE_VARIANT: Record<string, "success" | "secondary" | "warning"> = {
  high: "success",
  medium: "secondary",
  low: "warning",
};

const SEARCH_DEBOUNCE_MS = 300;

function displayName(entity: MemoryEntity): string {
  const name = entity.canonical_name;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

interface PendingInvalidation {
  entityId: string;
  assertion: MemoryAssertion;
  reason: InvalidateReason;
}

interface FactCardProps {
  assertion: MemoryAssertion;
  sourceRef?: MemoryRecordRef | "unavailable" | undefined;
  onResolveSource: (assertion: MemoryAssertion) => void;
  onInvalidate: (assertion: MemoryAssertion, reason: InvalidateReason) => void;
}

function FactCard({
  assertion,
  sourceRef,
  onResolveSource,
  onInvalidate,
}: FactCardProps) {
  const { t } = useTranslation();
  const invalidated = assertion.invalid_from !== null;
  const trustKey = TRUST_KEYS[assertion.trust_level];
  const rememberedAt = assertion.valid_from ?? assertion.created_at;

  return (
    <Card className={cn("border-border/70", invalidated && "opacity-60")}>
      <CardContent className="space-y-2 p-3">
        <p className="text-sm">{assertion.assertion_text}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {trustKey && !invalidated && (
            <Badge variant={TRUST_BADGE_VARIANT[assertion.trust_level]}>
              {t(trustKey)}
            </Badge>
          )}
          <span>
            {t("memory.rememberedOn", { date: formatAbsoluteTime(rememberedAt) })}
          </span>
          {invalidated && (
            <span>
              {t("memory.invalidatedOn", {
                date: formatAbsoluteTime(assertion.invalid_from),
              })}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {assertion.source_record_ids.length > 0 && sourceRef === undefined && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onResolveSource(assertion)}
            >
              {t("memory.source")}
            </Button>
          )}
          {sourceRef === "unavailable" && (
            <span className="text-xs text-muted-foreground">
              {t("memory.sourceUnavailable")}
            </span>
          )}
          {sourceRef !== undefined &&
            sourceRef !== "unavailable" &&
            (sourceRef.conversation_id ? (
              <Button asChild variant="ghost" size="sm">
                <Link to={`/c/${sourceRef.conversation_id}`}>
                  <ExternalLink className="h-3.5 w-3.5" />
                  {t("memory.openConversation")}
                </Link>
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">
                {t("memory.sourceUnavailable")}
              </span>
            ))}
          {!invalidated && (
            <div className="ml-auto flex gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onInvalidate(assertion, "forget")}
              >
                {t("memory.forget")}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => onInvalidate(assertion, "incorrect")}
              >
                {t("memory.incorrect")}
              </Button>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

interface EntityRowProps {
  entity: MemoryEntity;
  showHistory: boolean;
  sourceRefs: Record<string, MemoryRecordRef | "unavailable">;
  onResolveSource: (assertion: MemoryAssertion) => void;
  onInvalidate: (
    entityId: string,
    assertion: MemoryAssertion,
    reason: InvalidateReason
  ) => void;
  onFactLoadError: () => void;
}

function EntityRow({
  entity,
  showHistory,
  sourceRefs,
  onResolveSource,
  onInvalidate,
  onFactLoadError,
}: EntityRowProps) {
  const { t } = useTranslation();
  // The accordion panel unmounts its children while closed, so this row
  // only mounts when its entity is opened: the query is lazy by
  // construction, and the shared cache keeps the facts across
  // close/reopen and navigation (staleTime controls refetch).
  const { data, isPending, errorUpdateCount } = useQuery({
    queryKey: QUERY_KEYS.memoryEntity(entity.id),
    queryFn: () => getMemoryEntity(entity.id),
  });
  // Toast once per failed attempt made while this row is mounted; the
  // baseline ref suppresses toasts for errors already in the cache
  // when the row (re)mounts.
  const errorCountRef = useRef(errorUpdateCount);
  useEffect(() => {
    if (errorUpdateCount > errorCountRef.current) {
      errorCountRef.current = errorUpdateCount;
      onFactLoadError();
    }
  }, [errorUpdateCount, onFactLoadError]);

  const facts = data?.assertions;
  const visibleFacts =
    facts === undefined
      ? undefined
      : showHistory
        ? facts
        : facts.filter((a) => a.invalid_from === null);

  return (
    <div className="space-y-2 pb-3">
      {isPending || visibleFacts === undefined ? (
        <Skeleton className="h-16 w-full" />
      ) : visibleFacts.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("memory.noEntityFacts")}</p>
      ) : (
        visibleFacts.map((assertion) => (
          <FactCard
            key={assertion.id}
            assertion={assertion}
            sourceRef={sourceRefs[assertion.id]}
            onResolveSource={onResolveSource}
            onInvalidate={(a, reason) => onInvalidate(entity.id, a, reason)}
          />
        ))
      )}
    </div>
  );
}

export function MemoryPage() {
  const { t } = useTranslation();
  const { toasts, addToast, dismissToast } = useToasts();

  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [sourceRefs, setSourceRefs] = useState<
    Record<string, MemoryRecordRef | "unavailable">
  >({});
  const [pending, setPending] = useState<PendingInvalidation | null>(null);
  const [reauthFor, setReauthFor] = useState<PendingInvalidation | null>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const {
    data: entityList,
    isInitialLoading: loading,
    error: queryError,
  } = useQuery({
    queryKey: QUERY_KEYS.memory(debouncedQuery),
    queryFn: async () => {
      const result = debouncedQuery
        ? await searchMemoryEntities(debouncedQuery)
        : await listMemoryEntities();
      return result.entities;
    },
  });
  const entities = entityList ?? [];

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    if (searchTimer.current !== null) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setDebouncedQuery(value.trim());
    }, SEARCH_DEBOUNCE_MS);
  };

  const handleResolveSource = useCallback(async (assertion: MemoryAssertion) => {
    const recordId = assertion.source_record_ids[0];
    if (!recordId) return;
    try {
      const result = await resolveMemoryRecord(recordId);
      setSourceRefs((prev) => ({ ...prev, [assertion.id]: result.record }));
    } catch {
      setSourceRefs((prev) => ({ ...prev, [assertion.id]: "unavailable" }));
    }
  }, []);

  const invalidateMutation = useMutation({
    mutationFn: ({
      assertion,
      reason,
      token,
    }: {
      entityId: string;
      assertion: MemoryAssertion;
      reason: InvalidateReason;
      token?: string | undefined;
    }) => invalidateMemoryAssertion(assertion.id, reason, token),
    onSuccess: (result, vars) => {
      if (!result.invalidated) {
        addToast({ type: "error", message: t("memory.invalidateFailed") });
        return;
      }
      addToast({ type: "success", message: t("memory.invalidated") });
      void queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.memoryEntity(vars.entityId),
      });
    },
    onError: () => {
      addToast({ type: "error", message: t("memory.invalidateFailed") });
    },
  });

  const handleReauthSuccess = (token?: string) => {
    const target = reauthFor;
    setReauthFor(null);
    if (!target) return;
    invalidateMutation.mutate({
      entityId: target.entityId,
      assertion: target.assertion,
      reason: target.reason,
      token,
    });
  };

  return (
    <AdminLayout title={t("memory.title")}>
      <ScrollArea className="h-full">
        <div className="space-y-4 px-4 py-4">
          <p className="text-sm text-muted-foreground">{t("memory.subtitle")}</p>

          <div className="flex flex-wrap items-center gap-4">
            <div className="relative min-w-56 flex-1">
              <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={searchQuery}
                onChange={(e) => handleSearchChange(e.target.value)}
                placeholder={t("memory.searchPlaceholder")}
                className="pl-8"
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch
                id="memory-show-history"
                checked={showHistory}
                onCheckedChange={setShowHistory}
              />
              <Label htmlFor="memory-show-history" className="text-sm">
                {t("memory.showHistory")}
              </Label>
            </div>
          </div>

          {loading && (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-2/3" />
            </div>
          )}

          {!loading && queryError !== null && (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>{errorToDisplayText(queryError, t)}</EmptyTitle>
              </EmptyHeader>
            </Empty>
          )}

          {!loading && queryError === null && entities.length === 0 && (
            <Empty>
              <EmptyHeader>
                <Brain className="h-8 w-8 text-muted-foreground" />
                <EmptyTitle>
                  {searchQuery
                    ? t("memory.noSearchResults", { query: searchQuery })
                    : t("memory.title")}
                </EmptyTitle>
                {!searchQuery && (
                  <EmptyDescription>{t("memory.noFacts")}</EmptyDescription>
                )}
              </EmptyHeader>
            </Empty>
          )}

          {!loading && queryError === null && entities.length > 0 && (
            <Accordion>
              {entities.map((entity) => {
                const typeKey = ENTITY_TYPE_KEYS[entity.entity_type];
                return (
                  <AccordionItem key={entity.id} value={entity.id}>
                    <AccordionTrigger>
                      <span className="flex flex-1 items-center gap-2 text-left">
                        <span className="font-medium">{displayName(entity)}</span>
                        <Badge variant="secondary">
                          {typeKey ? t(typeKey) : entity.entity_type}
                        </Badge>
                        {typeof entity.active_assertions === "number" && (
                          <span className="text-xs text-muted-foreground">
                            {t("memory.factsCount", {
                              count: entity.active_assertions,
                            })}
                          </span>
                        )}
                      </span>
                    </AccordionTrigger>
                    <AccordionContent>
                      <EntityRow
                        entity={entity}
                        showHistory={showHistory}
                        sourceRefs={sourceRefs}
                        onResolveSource={handleResolveSource}
                        onInvalidate={(entityId, assertion, reason) =>
                          setPending({ entityId, assertion, reason })
                        }
                        onFactLoadError={() =>
                          addToast({
                            type: "error",
                            message: t("memory.loadFailed"),
                          })
                        }
                      />
                    </AccordionContent>
                  </AccordionItem>
                );
              })}
            </Accordion>
          )}
        </div>
      </ScrollArea>

      <AlertDialog
        open={pending !== null}
        onOpenChange={(open: boolean) => {
          if (!open) setPending(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pending?.reason === "incorrect"
                ? t("memory.incorrectConfirmTitle")
                : t("memory.forgetConfirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pending?.reason === "incorrect"
                ? t("memory.incorrectConfirmDescription")
                : t("memory.forgetConfirmDescription")}
            </AlertDialogDescription>
            {pending && (
              <p className="text-sm text-muted-foreground">
                {pending.assertion.assertion_text}
              </p>
            )}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setPending(null)}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setReauthFor(pending);
                setPending(null);
              }}
            >
              {t("common.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {reauthFor && (
        <ReauthModal
          onSuccess={(token) => handleReauthSuccess(token)}
          onClose={() => setReauthFor(null)}
        />
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </AdminLayout>
  );
}
