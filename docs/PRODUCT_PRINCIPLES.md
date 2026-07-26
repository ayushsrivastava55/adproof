# Product Principles

## 1. Evidence before conclusion

The product should never lead with a score and hide the underlying media. Every important conclusion must be traceable to exact playable moments.

## 2. Honest uncertainty

A system that labels ambiguity correctly is more useful than one that produces confident but unsupported answers.

The interface must distinguish:

- evidence found;
- evidence not found;
- media not fully processed;
- low-quality media;
- unsupported rule;
- conflicting evidence;
- human interpretation required.

## 3. Deterministic measurement

Language models and multimodal models may retrieve or describe possible evidence. Application logic must calculate:

- time windows;
- merged intervals;
- total duration;
- counts;
- thresholds;
- ordering;
- state transitions.

## 4. Human confirmation of obligations

A campaign brief can contain ambiguity, contradictions, and subjective language. Generated rules are proposals until confirmed by an authorized user.

## 5. Focused indexing

Use narrow, domain-specific visual indexes rather than one generic “describe everything” index. Separate product presence, disclosure text, prohibited claims, competitors, and product-use actions when doing so improves reliability.

## 6. No invisible mock behavior

A missing integration must produce an integration error, not a polished fictional result. Test fixtures must be clearly marked.

## 7. Auditability by default

Record:

- what the system evaluated;
- which version of the rule was active;
- what evidence was retrieved;
- what recommendation was generated;
- who reviewed it;
- what was overridden;
- why it was overridden.

## 8. Reviewer efficiency without reviewer manipulation

The interface should reduce review time while avoiding presentation bias. Supporting and conflicting evidence should be equally discoverable.

## 9. Reversible automation

Automated recommendations must be reversible. Human decisions and corrections should improve evaluation without rewriting historical facts.

## 10. Customer-configured risk

Different customers have different tolerance for auto-approval, auto-rejection, and uncertainty. Risk policy belongs to the workspace and campaign configuration.

## 11. Source preservation

Original media, original brief text, and original rule versions are primary audit artifacts. Derived data must not replace them.

## 12. Integration resilience

External processing is asynchronous and fallible. The product must treat waiting, retry, partial completion, failure, and cancellation as normal states.
