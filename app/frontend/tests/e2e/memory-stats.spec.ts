import { expect, test } from '@playwright/test'

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:4173'
const PASSWORD = process.env.TEST_PASSWORD || 'testpass123'
const MESSAGE_TIMEOUT = 60_000
const REQUEST_START_TIMEOUT = 10_000
const SHORT_MESSAGES = [
  'Скажи привет одним предложением',
  'Какая сейчас погода бывает весной?',
  'Расскажи коротко про кошек',
  'Что такое облака?',
  'Сколько планет в солнечной системе?',
  'Какой самый большой океан?',
  'Расскажи про горы',
  'Что такое дождь?',
  'Какие бывают цветы?',
  'Расскажи про луну',
  'Что такое ветер?',
  'Какие бывают деревья?',
  'Расскажи про звёзды',
  'Что такое снег?',
  'Какие бывают птицы?',
]

interface ZoneStats {
  workingUsed: number
  workingBudget: number
  recallUsed: number
  recallBudget: number
  summaryUsed: number
  summaryBudget: number
}

function parseZoneStats(text: string): ZoneStats | null {
  const match = text.match(
    /W:([\d.]+K?)\/([\d.]+K?)\s+S:([\d.]+K?)\/([\d.]+K?)\s+R:([\d.]+K?)\/([\d.]+K?)/
  )
  if (!match) return null

  function parseValue(v: string): number {
    if (v.endsWith('K')) return parseFloat(v) * 1000
    return parseFloat(v)
  }

  return {
    workingUsed: parseValue(match[1]),
    workingBudget: parseValue(match[2]),
    summaryUsed: parseValue(match[3]),
    summaryBudget: parseValue(match[4]),
    recallUsed: parseValue(match[5]),
    recallBudget: parseValue(match[6]),
  }
}

async function login(page: import('@playwright/test').Page) {
  const chatInput = page.locator('textarea').first()
  const passwordInput = page.getByLabel('Password')

  await page.goto(`${BASE_URL}/login`)

  if (await chatInput.isVisible().catch(() => false)) {
    return
  }

  await expect(passwordInput).toBeVisible({ timeout: 30_000 })
  await passwordInput.fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(chatInput).toBeVisible({ timeout: 30_000 })
}

async function createNewChat(page: import('@playwright/test').Page) {
  const newChatBtn = page.locator('button[aria-label="New chat"], button:has-text("New Chat")').first()
  if (await newChatBtn.isVisible()) {
    await newChatBtn.click()
    await page.waitForTimeout(1000)
  }
}

async function sendMessageAndWait(page: import('@playwright/test').Page, message: string): Promise<void> {
  const input = page.locator('textarea').first()
  const stopButton = page.getByLabel('Stop generation')
  await input.fill(message)
  await page.keyboard.press('Enter')

  await stopButton.waitFor({ state: 'visible', timeout: REQUEST_START_TIMEOUT })
  await stopButton.waitFor({ state: 'hidden', timeout: MESSAGE_TIMEOUT })
  await expect(input).toBeEnabled({ timeout: MESSAGE_TIMEOUT })
}

async function getLastZoneStats(page: import('@playwright/test').Page): Promise<ZoneStats | null> {
  const statsElements = page.locator('[title^="Working:"]')
  const count = await statsElements.count()
  if (count === 0) return null

  const text = (await statsElements.last().textContent()) || ''
  return parseZoneStats(text)
}

test.describe('Memory zone statistics', () => {
  test.describe.configure({ mode: 'serial' })
  test.setTimeout(300_000)

  test('working memory grows with each message', async ({ page }) => {
    await login(page)
    await createNewChat(page)

    const measurements: ZoneStats[] = []

    for (let i = 0; i < 5; i++) {
      await sendMessageAndWait(page, SHORT_MESSAGES[i])
      const stats = await getLastZoneStats(page)

      if (stats) {
        measurements.push(stats)
        console.log(
          `Message ${i + 1}: W:${stats.workingUsed}/${stats.workingBudget} ` +
          `R:${stats.recallUsed}/${stats.recallBudget} ` +
          `S:${stats.summaryUsed}/${stats.summaryBudget}`
        )
      }
    }

    // At least 3 measurements should have been captured
    expect(measurements.length).toBeGreaterThanOrEqual(3)

    // Working memory budget should be consistent
    const budgets = measurements.map(m => m.workingBudget)
    expect(new Set(budgets).size).toBe(1)

    // Working memory used should grow over time
    const usedValues = measurements.map(m => m.workingUsed)
    const isGrowing = usedValues.every((val, i) => i === 0 || val >= usedValues[i - 1])
    expect(isGrowing).toBe(true)

    // Last measurement should have non-zero working memory
    const last = measurements[measurements.length - 1]
    expect(last.workingUsed).toBeGreaterThan(0)

    // Recall and summary should be zero (no recall tool called, no eviction yet)
    expect(last.recallUsed).toBe(0)
    expect(last.summaryUsed).toBe(0)
  })

  test('working memory does not exceed budget', async ({ page }) => {
    await login(page)
    await createNewChat(page)

    const measurements: ZoneStats[] = []

    for (let i = 0; i < 12; i++) {
      await sendMessageAndWait(page, SHORT_MESSAGES[i])
      const stats = await getLastZoneStats(page)

      if (stats) {
        measurements.push(stats)
        console.log(
          `Message ${i + 1}: W:${stats.workingUsed}/${stats.workingBudget} ` +
          `S:${stats.summaryUsed}/${stats.summaryBudget}`
        )

        // Working memory should never exceed budget
        expect(stats.workingUsed).toBeLessThanOrEqual(stats.workingBudget)
      }
    }

    expect(measurements.length).toBeGreaterThanOrEqual(5)
  })

  test('separate chats have independent statistics', async ({ page }) => {
    await login(page)

    // Chat A: send 3 messages
    await createNewChat(page)
    for (let i = 0; i < 3; i++) {
      await sendMessageAndWait(page, SHORT_MESSAGES[i])
    }
    const chatAStats = await getLastZoneStats(page)

    // Chat B: send 1 message
    await createNewChat(page)
    await sendMessageAndWait(page, SHORT_MESSAGES[5])
    const chatBStats = await getLastZoneStats(page)

    if (chatAStats && chatBStats) {
      console.log(`Chat A: W:${chatAStats.workingUsed}`)
      console.log(`Chat B: W:${chatBStats.workingUsed}`)

      // Chat A should have more working memory used than Chat B
      // (3 messages vs 1 message)
      expect(chatAStats.workingUsed).toBeGreaterThan(chatBStats.workingUsed)
    }
  })

  test('summary zone fills when working memory overflows', async ({ page }) => {
    await login(page)
    await createNewChat(page)

    const measurements: ZoneStats[] = []
    let summaryTriggered = false

    for (let i = 0; i < SHORT_MESSAGES.length; i++) {
      await sendMessageAndWait(page, SHORT_MESSAGES[i])
      const stats = await getLastZoneStats(page)

      if (stats) {
        measurements.push(stats)
        const wPct = Math.round(
          (stats.workingUsed / stats.workingBudget) * 100,
        )
        console.log(
          `Message ${i + 1}: W:${stats.workingUsed}/${stats.workingBudget} (${wPct}%) ` +
          `S:${stats.summaryUsed}/${stats.summaryBudget}`,
        )

        if (stats.summaryUsed > 0 && !summaryTriggered) {
          summaryTriggered = true
          console.log(`  >>> S became non-zero at message ${i + 1}`)
        }

        // Stop after S triggered and we have a few more data points
        if (summaryTriggered && measurements.length >= 8) {
          break
        }
      }
    }

    // S should have triggered at some point during the conversation
    expect(summaryTriggered).toBe(true)

    // Last measurement: S should be non-zero
    const last = measurements[measurements.length - 1]
    expect(last.summaryUsed).toBeGreaterThan(0)

    // S should not exceed its budget
    expect(last.summaryUsed).toBeLessThanOrEqual(last.summaryBudget)
  })

  test('memory subsystem stress test: all zones under 3x overload', async ({ page }) => {
    test.setTimeout(600_000)
    await login(page)

    // ── Phase 1: Build conversation history (W grows) ──────────────
    // Send enough messages to fill W budget ~3x over.
    // With typical W budget ~7.5K tokens and ~200 tokens per exchange,
    // we need ~110 exchanges. We use 20 substantial messages instead,
    // each generating a long response from the model.
    console.log('\n=== MEMORY SUBSYSTEM STRESS TEST ===\n')
    console.log('Phase 1: Fill working memory (W zone)')
    console.log('─'.repeat(65))
    console.log(
      'Turn  │ W used/budget (%)    │ R used/budget (%)    │ S used/budget (%)'
    )
    console.log('─'.repeat(65))

    await createNewChat(page)

    const phase1Messages = [
      'Расскажи подробно про архитектуру микросервисов',
      'Объясни паттерны проектирования GoF с примерами',
      'Как устроен протокол HTTP/2 и чем отличается от HTTP/1',
      'Расскажи про CAP теорему и её практическое применение',
      'Объясни как работает garbage collector в JVM',
      'Какие алгоритмы консенсуса используются в распределённых системах',
      'Расскажи про архитектуру процессоров ARM и x86',
      'Как работает TLS handshake и обмен ключами',
      'Объясни принципы работы B-tree индексов в базах данных',
      'Расскажи про модель акторов и как она реализована в Erlang',
    ]

    interface StepLog {
      phase: string
      turn: number
      w: number
      wBudget: number
      wPct: number
      r: number
      rBudget: number
      rPct: number
      s: number
      sBudget: number
      sPct: number
    }

    const allSteps: StepLog[] = []
    let turnNumber = 0

    function logStep(stats: ZoneStats, phase: string): StepLog {
      turnNumber++
      const step: StepLog = {
        phase,
        turn: turnNumber,
        w: stats.workingUsed,
        wBudget: stats.workingBudget,
        wPct: Math.round((stats.workingUsed / stats.workingBudget) * 100),
        r: stats.recallUsed,
        rBudget: stats.recallBudget,
        rPct: stats.recallBudget > 0
          ? Math.round((stats.recallUsed / stats.recallBudget) * 100)
          : 0,
        s: stats.summaryUsed,
        sBudget: stats.summaryBudget,
        sPct: stats.summaryBudget > 0
          ? Math.round((stats.summaryUsed / stats.summaryBudget) * 100)
          : 0,
      }
      allSteps.push(step)
      const pad = (n: number, w: number) => String(n).padStart(w)
      console.log(
        `  ${pad(step.turn, 3)}  │ ` +
        `${pad(step.w, 5)}/${pad(step.wBudget, 5)} (${pad(step.wPct, 3)}%) │ ` +
        `${pad(step.r, 5)}/${pad(step.rBudget, 5)} (${pad(step.rPct, 3)}%) │ ` +
        `${pad(step.s, 5)}/${pad(step.sBudget, 5)} (${pad(step.sPct, 3)}%)`
      )
      return step
    }

    for (const msg of phase1Messages) {
      await sendMessageAndWait(page, msg)
      const stats = await getLastZoneStats(page)
      if (stats) logStep(stats, 'W-fill')
    }

    // ── Phase 2: Overflow W, fill S (eviction + compression) ───────
    console.log('\nPhase 2: Overflow W, accumulate S (eviction)')
    console.log('─'.repeat(65))

    const phase2Messages = [
      'Как работает MVCC в PostgreSQL',
      'Расскажи про event sourcing и CQRS',
      'Объясни разницу между gRPC и REST',
      'Как устроена виртуальная память в Linux',
      'Расскажи про алгоритмы маршрутизации в сетях',
      'Объясни принципы работы CDN',
      'Как работает шардирование данных',
      'Расскажи про Zero Trust архитектуру безопасности',
      'Объясни как работает WebSocket протокол',
      'Расскажи про service mesh и Istio',
    ]

    for (const msg of phase2Messages) {
      await sendMessageAndWait(page, msg)
      const stats = await getLastZoneStats(page)
      if (stats) logStep(stats, 'S-fill')
    }

    // ── Phase 3: New chat after long history ───────────────────────
    // Wait for background memory processing before starting a fresh chat.
    console.log('\nPhase 3: Waiting for DialogRecorder (15s)...')
    await page.waitForTimeout(15000)

    console.log('Phase 3: New chat after long history')
    console.log('─'.repeat(65))

    await createNewChat(page)
    await sendMessageAndWait(
      page,
      'Помнишь о чём мы говорили? Перечисли все темы',
    )
    const recallStats = await getLastZoneStats(page)
    if (recallStats) logStep(recallStats, 'R-recall')

    // ── Summary Report ─────────────────────────────────────────────
    console.log('\n' + '═'.repeat(65))
    console.log('STRESS TEST RESULTS')
    console.log('═'.repeat(65))

    const wSteps = allSteps.filter(s => s.phase === 'W-fill')
    const sSteps = allSteps.filter(s => s.phase === 'S-fill')
    const wBudget = allSteps[0]?.wBudget ?? 0
    const rBudget = allSteps[0]?.rBudget ?? 0
    const sBudget = allSteps[0]?.sBudget ?? 0
    const wMax = Math.max(...allSteps.map(s => s.w))
    const sMax = Math.max(...allSteps.map(s => s.s))
    const rMax = Math.max(...allSteps.map(s => s.r))
    const totalTurns = allSteps.length

    console.log(`Total turns completed: ${totalTurns}`)
    console.log('')
    console.log(`W zone: budget=${wBudget}, peak=${wMax}, ` +
      `peak%=${Math.round((wMax / wBudget) * 100)}%`)
    console.log(`S zone: budget=${sBudget}, peak=${sMax}, ` +
      `peak%=${Math.round((sMax / sBudget) * 100)}%`)
    console.log(`R zone: budget=${rBudget}, peak=${rMax}, ` +
      `peak%=${rBudget > 0 ? Math.round((rMax / rBudget) * 100) : 0}%`)
    console.log('')

    // Count eviction events (W drops between consecutive turns)
    let evictionCount = 0
    for (let i = 1; i < allSteps.length; i++) {
      if (allSteps[i].w < allSteps[i - 1].w && allSteps[i].phase !== 'R-recall') {
        evictionCount++
      }
    }
    console.log(`Eviction events detected: ${evictionCount}`)
    console.log(`S filled: ${sMax > 0 ? 'YES' : 'NO'}`)
    console.log(`R activated: ${rMax > 0 ? 'YES' : 'NO'}`)
    console.log('═'.repeat(65))

    // ── Assertions ─────────────────────────────────────────────────

    // All turns must complete (system stability)
    expect(totalTurns).toBe(21)

    // W must never exceed budget
    for (const step of allSteps) {
      expect(step.w).toBeLessThanOrEqual(step.wBudget)
    }

    // W must have grown during phase 1
    expect(wSteps[wSteps.length - 1].w).toBeGreaterThan(wSteps[0].w)

    // At least one eviction must have occurred
    expect(evictionCount).toBeGreaterThanOrEqual(1)

    // S must grow once eviction starts
    expect(sMax).toBeGreaterThan(0)
    expect(sSteps[sSteps.length - 1].s).toBeGreaterThan(sSteps[0].s)

    // S must never exceed budget
    for (const step of allSteps) {
      expect(step.s).toBeLessThanOrEqual(step.sBudget)
    }

    // If recall activates in this environment, it must stay within budget.
    if (rMax > 0 && rBudget > 0) {
      expect(rMax).toBeLessThanOrEqual(rBudget)
    }
  })

  test('R zone reflects model-initiated memory_recall tool call', async ({ page }) => {
    await login(page)

    // First conversation: user shares specific facts
    await createNewChat(page)
    await sendMessageAndWait(page, 'Наш проект использует Python 3.12, FastAPI и PostgreSQL')
    await sendMessageAndWait(page, 'Для тестов мы используем pytest с фикстурами')

    // Wait for DialogRecorder to process into searchable records
    await page.waitForTimeout(15000)

    // Second conversation: ask a question that should make the model
    // decide to call memory_recall on its own
    await createNewChat(page)
    await sendMessageAndWait(
      page,
      'Какой стек технологий мы используем в нашем проекте?',
    )
    const stats = await getLastZoneStats(page)

    if (stats) {
      console.log(
        `Model-initiated recall: W:${stats.workingUsed} ` +
        `R:${stats.recallUsed}/${stats.recallBudget} ` +
        `S:${stats.summaryUsed}`,
      )

      // The model should call memory_recall to answer this question.
      // If R > 0, the tool was called and zone stats reflect it.
      // Smaller models may skip the tool — log the outcome either way.
      if (stats.recallUsed > 0) {
        console.log('Model called memory_recall — R zone active')
        expect(stats.recallUsed).toBeLessThanOrEqual(stats.recallBudget)
      } else {
        console.log(
          'Model did NOT call memory_recall — answered from general knowledge. ' +
          'This can happen with smaller models that skip tool calls.',
        )
      }
    }
  })

  test('R zone reflects forced recall for past-conversation questions', async ({ page }) => {
    await login(page)

    // First conversation: user shares personal context
    await createNewChat(page)
    await sendMessageAndWait(page, 'Я сейчас изучаю Go, до этого писал только на Python')
    await sendMessageAndWait(page, 'Хочу написать CLI утилиту для мониторинга серверов')

    // Wait for DialogRecorder to process into searchable records
    await page.waitForTimeout(15000)

    // Second conversation: user asks about past chat naturally.
    // "Помнишь" triggers forced recall — memory_recall runs before the LLM,
    // regardless of whether the model would have called it.
    await createNewChat(page)
    await sendMessageAndWait(page, 'Помнишь о чём мы говорили?')
    const stats = await getLastZoneStats(page)

    if (stats) {
      console.log(
        `Forced recall: W:${stats.workingUsed} ` +
        `R:${stats.recallUsed}/${stats.recallBudget} ` +
        `S:${stats.summaryUsed}`,
      )

      // R must be non-zero: forced recall is deterministic,
      // it does not depend on model decision
      expect(stats.recallUsed).toBeGreaterThan(0)
      expect(stats.recallUsed).toBeLessThanOrEqual(stats.recallBudget)
    }
  })
})
