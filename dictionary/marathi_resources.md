# Marathi-English Resources for LLMs and Translation

If you're building an **LLM, RAG system, tokenizer, bilingual embedding model, or translation pipeline**, you should distinguish between **lexical authority** and **machine-learning usefulness**.

Here's how I'd rank the available Marathi-English resources.

| Resource                                  | Quality | Best use                                     |
| ----------------------------------------- | ------- | -------------------------------------------- |
| **Molesworth Marathi-English Dictionary** | ★★★★★   | Gold-standard lexical resource               |
| **Oxford Marathi-English Dictionary**     | ★★★★★   | Modern vocabulary, clean definitions         |
| **SHABDKOSH**                             | ★★★★☆   | Everyday vocabulary, examples, pronunciation |
| **Maharashtra Government Dictionary**     | ★★★★☆   | Official terminology                         |
| **Marathi WordNet**                       | ★★★★☆   | Synonyms, semantic graphs                    |
| **Glosbe**                                | ★★★☆☆   | Parallel examples (noisy but useful)         |

## 1. Molesworth Dictionary (Best overall)

The classic **Molesworth Marathi-English Dictionary** is still regarded as the most comprehensive Marathi-English lexical work.

Pros
* ~60k+ entries
* Excellent sense distinctions
* Rich idioms
* Morphological variants
* Many traditional words modern dictionaries omit

Cons
* Victorian English
* Old spelling conventions
* No modern technical vocabulary

For LLMs this is excellent for:
* lexicon construction
* bilingual alignment
* semantic lookup
* evaluation datasets

---

## 2. Oxford Marathi-English

If you're working with **modern Marathi**, Oxford is probably the best commercial dictionary.

Pros
* contemporary usage
* standardized spellings
* better treatment of modern words
* cleaner definitions than Molesworth

It is especially useful if your LLM is intended for today's Marathi speakers rather than historical texts. ([Oxford Languages](https://languages.oup.com/google-dictionary-mr-en/))

---

## 3. SHABDKOSH

Probably the best freely accessible online dictionary.

Pros
* examples
* pronunciations
* idioms
* inflected forms
* English ↔ Marathi

Good for:
* retrieval
* data augmentation
* grounding translations

Not as authoritative as Oxford/Molesworth but very practical.

---

## 4. Maharashtra Government Dictionary

Very useful for
* legal
* administrative
* academic
* official terminology

If you're making an assistant for government or education, definitely include it.

---

## 5. Marathi WordNet

Not really a dictionary.

Instead it gives
* synonym sets
* hypernyms
* hyponyms
* semantic relations

Very useful for
* retrieval expansion
* semantic search
* embedding evaluation
* knowledge graphs

---

# If you're training an LLM

A dictionary alone is **not enough**. I'd combine:

### Lexical layer
* Molesworth
* Oxford
* SHABDKOSH
* Marathi WordNet

### Parallel data
* OPUS
* AI4Bharat IndicCorp
* FLORES-200
* Samanantar

### Monolingual corpus
* **L3Cube MahaCorpus**
* Marathi Wikipedia
* Marathi news
* books

L3Cube's MahaCorpus is currently the strongest openly available large-scale Marathi corpus, with tens of millions of sentences, and is widely used for Marathi language models. ([ACL Anthology](https://aclanthology.org/2022.wildre-1.17/))

---

## If your goal is the highest-quality Marathi-English lexical database

I'd build it by merging:
1. **Molesworth** (core lexicon)
2. **Oxford** (modern vocabulary)
3. **SHABDKOSH** (usage examples)
4. **Marathi WordNet** (semantic relations)
5. **L3Cube MahaCorpus** (frequency statistics)
6. **Parallel corpora** (real translations)

That combination will significantly outperform any single dictionary for LLM applications such as translation, RAG, bilingual embeddings, or instruction tuning.
