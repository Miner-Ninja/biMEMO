/**
 * One catalog setting rendered as a card with the right control.
 *
 * The control type is picked from `SecuritySetting.type`:
 *   - toggle      -> Switch
 *   - slider_int  -> Slider (integer values)
 *   - slider_float-> Slider (float values)
 *   - enum        -> RadioGroup (<= 4 options) or Select (otherwise)
 *   - list_string -> ListEditor (badge chips + add input)
 *   - text        -> Textarea
 *
 * When a protective setting is moved into a less-safe state the
 * card surfaces an inline `<Alert variant="destructive">` warning
 * (soft-friction model: no modal, no unlock).
 */

import { AlertTriangle } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  RadioGroup,
  RadioGroupItem,
} from "@/components/ui/radio-group"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type {
  SecuritySetting,
  SecuritySettingValue,
} from "@/api/security"
import { RiskDot } from "./RiskDot"
import { ListEditor } from "./ListEditor"
import { PAGE_COPY } from "../i18n/ru"

interface SettingItemProps {
  setting: SecuritySetting
  value: SecuritySettingValue
  baselineValue: SecuritySettingValue | undefined
  onChange: (next: SecuritySettingValue) => void
}

const ENUM_RADIO_THRESHOLD = 4

export function SettingItem({
  setting,
  value,
  baselineValue,
  onChange,
}: SettingItemProps) {
  const isLessSafe = isSettingLessSafeThanBaseline(setting, value, baselineValue)
  const inputId = `setting-${setting.key}`

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-2 min-w-0">
            <RiskDot level={setting.risk_level} className="mt-1.5" />
            <div className="flex flex-col gap-1 min-w-0">
              <label
                htmlFor={inputId}
                className="text-sm font-medium leading-tight"
              >
                {setting.label}
              </label>
              <p className="text-xs text-muted-foreground leading-snug">
                {setting.description}
              </p>
              {setting.example ? (
                <p className="text-xs text-muted-foreground leading-snug italic">
                  💡 {setting.example}
                </p>
              ) : null}
            </div>
          </div>
          <SettingControl
            setting={setting}
            value={value}
            inputId={inputId}
            onChange={onChange}
          />
        </div>
        {isLessSafe ? (
          <Alert variant="destructive" className="py-2">
            <AlertTriangle />
            <AlertDescription>{PAGE_COPY.whenOff}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  )
}

interface SettingControlProps {
  setting: SecuritySetting
  value: SecuritySettingValue
  inputId: string
  onChange: (next: SecuritySettingValue) => void
}

function SettingControl({
  setting,
  value,
  inputId,
  onChange,
}: SettingControlProps) {
  switch (setting.type) {
    case "toggle":
      return (
        <Switch
          id={inputId}
          checked={Boolean(value)}
          onCheckedChange={(next: boolean) => onChange(next)}
        />
      )

    case "slider_int":
    case "slider_float":
      return (
        <SliderControl
          setting={setting}
          inputId={inputId}
          value={typeof value === "number" ? value : 0}
          onChange={onChange}
        />
      )

    case "enum":
      return (
        <EnumControl
          setting={setting}
          value={typeof value === "string" ? value : ""}
          inputId={inputId}
          onChange={onChange}
        />
      )

    case "list_string":
      return (
        <div className="w-full">
          <ListEditor
            values={Array.isArray(value) ? value : []}
            placeholder={setting.constraints.placeholder ?? undefined}
            onChange={(next) => onChange(next)}
          />
        </div>
      )

    case "text":
      return (
        <Textarea
          id={inputId}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
          rows={3}
          maxLength={setting.constraints.max_length ?? undefined}
          className="w-full"
        />
      )
  }
}

interface SliderControlProps {
  setting: SecuritySetting
  value: number
  inputId: string
  onChange: (next: SecuritySettingValue) => void
}

function SliderControl({
  setting,
  value,
  inputId,
  onChange,
}: SliderControlProps) {
  const { min, max, step, unit } = setting.constraints
  const isInt = setting.type === "slider_int"
  return (
    <div className="flex items-center gap-3 min-w-[180px]">
      <Slider
        id={inputId}
        value={[value]}
        min={min ?? 0}
        max={max ?? 100}
        step={step ?? (isInt ? 1 : 0.1)}
        onValueChange={(next: number[]) => {
          const raw = next[0] ?? 0
          onChange(isInt ? Math.round(raw) : raw)
        }}
        className="w-32"
      />
      <Badge variant="secondary" className="font-mono text-xs whitespace-nowrap">
        {value}
        {unit ? ` ${unit}` : ""}
      </Badge>
    </div>
  )
}

interface EnumControlProps {
  setting: SecuritySetting
  value: string
  inputId: string
  onChange: (next: SecuritySettingValue) => void
}

function EnumControl({
  setting,
  value,
  inputId,
  onChange,
}: EnumControlProps) {
  const options = setting.constraints.options ?? []
  if (options.length <= ENUM_RADIO_THRESHOLD) {
    return (
      <RadioGroup
        id={inputId}
        value={value}
        onValueChange={onChange}
        className="flex flex-col gap-1.5"
      >
        {options.map((option) => (
          <div key={option.value} className="flex items-center gap-2">
            <RadioGroupItem
              id={`${inputId}-${option.value}`}
              value={option.value}
            />
            <label
              htmlFor={`${inputId}-${option.value}`}
              className="text-sm"
            >
              {option.label}
            </label>
          </div>
        ))}
      </RadioGroup>
    )
  }
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={inputId} className="min-w-[180px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

function isSettingLessSafeThanBaseline(
  setting: SecuritySetting,
  value: SecuritySettingValue,
  baselineValue: SecuritySettingValue | undefined,
): boolean {
  // The "less safe" inline warning fires only when a protective
  // toggle moves to OFF (toggle == false). Sliders, enums, and
  // list-of-string changes do not surface a generic warning —
  // their effect depends on the direction and is not always
  // weakening security.
  if (setting.type !== "toggle") return false
  if (setting.risk_level === "low") return false
  if (typeof value !== "boolean") return false
  if (value) return false
  if (baselineValue === undefined) return false
  if (typeof baselineValue !== "boolean") return false
  return baselineValue === true
}
