## Memory Architecture Refactor

### Current pipeline

The current memory pipeline is a three-stage compression flow:

1. `messages`
   - Raw conversation history.
   - Stores original role/content payloads.
   - Useful for exact recall, but scoped to `conversation_id`.

2. `observations`
   - Structured compression of messages.
   - Stores `summary`, `key_facts`, `action_items`,
     `conversation_id`, `session_id`, and taint status.
   - Indexed for FTS and embedded for semantic search.

3. `reflections`
   - Higher-level synthesis from observations.
   - Stores `main_point`, `based_on`, `trust_level`,
     and `source_observation_ids`.

### Current architectural gaps

1. Missing provenance between `messages` and `observations`
   - Observations do not store `source_message_ids`.
   - This weakens traceability, explainability, and debugging.

2. Downstream underuses structured observation data
   - Embeddings rely on observation `summary` only.
   - Reflector synthesizes from observation `summary` only.
   - `key_facts` and `action_items` are stored but underused.

3. Reflection trust level is inconsistent
   - The reflection model uses `high|medium|low`.
   - The schema stores `trust_level` as a legacy numeric value.
   - Context loading sorts by trust level, but the representation
     is not semantically stable.

4. Structured fields are only partially filtered
   - Output filtering focuses on `summary` and `main_point`.
   - `key_facts`, `action_items`, and `based_on` need
     equivalent filtering discipline.

5. Web chat uses `session_id` as `conversation_id`
   - Raw episodic memory is fragmented by auth session.
   - Semantic memory outlives episodic memory too aggressively.

### Refactor plan

#### Stage 1: Observation provenance

- Add `source_message_ids` to `observations`.
- Carry provenance through:
  - schema
  - DB proxy params and handlers
  - orchestrator client
  - observer pipeline
  - debug/admin memory responses
- Preserve backward compatibility for existing rows with `[]`.

#### Stage 2: Reflection trust-level normalization

- Keep label semantics for prompts and debug output.
- Add a sortable numeric representation:
  - `high = 3`
  - `medium = 2`
  - `low = 1`
- Use numeric ordering in context loading and admin listing.

#### Stage 3: Use full observation payload downstream

- Build embeddings from:
  - `summary`
  - `key_facts`
  - `action_items`
- Feed reflector the full observation payload instead of summary only.

#### Stage 4: Structured-field filtering

- Apply filtering to:
  - observation `key_facts`
  - observation `action_items`
  - reflection `based_on`

#### Stage 5: Decouple `conversation_id` from `session_id`

- Introduce stable web conversation identifiers.
- Keep `session_id` for security and taint semantics only.

### Recommended implementation order

1. Observation provenance
2. Reflection trust-level normalization
3. Full observation payload in embeddings and reflector input
4. Structured-field filtering
5. Conversation/session separation
