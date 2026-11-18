# Phase 2 sweep results

47/47 sites run. Exit codes: 0: 36, 2: 8, 4: 3

Runtime: median 85 s, max 1036 s, over 240 s: 3

| Site | Cat | Exit | s | Pages | Profile | C/H/M/L | Masked | Recs | Root | Critical + high |
| :-- | :-- | :-: | :-: | :-: | :-- | :-: | :-: | :-: | :-: | :-- |
| adobe | saas_software | 2 | 116 |  |  |  |  |  |  | (no report) + 115.66s    - F-015 (G3.ENTITY.WEAK_DISAMBIGUATION) evidence_refs[1] jsonpath: path '$.@graph[0].publisher.name' does not yield '' |
| boat | ecommerce | 0 | 182 | 30 | ecommerce(medium) | 0/2/11/6 | 0 | 5 |  | H:G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES; H:G2.SD.REQUIRED_PROP_MISSING |
| mamaearth | ecommerce | 0 | 113 | 30 | ecommerce(high) | 0/2/12/11 | 0 | 4 |  | H:G0.SITEMAP.STALE_OR_TRUNCATED; H:G2.SD.REQUIRED_PROP_MISSING |
| nykaa | ecommerce | 4 | 2 |  |  |  |  |  |  | (no report) +   1.75s  homepage unreachable: network_error HTTPException: got more than 100 headers |
| ikea_in | ecommerce | 0 | 107 | 30 | ecommerce(high) | 0/1/12/8 | 0 | 4 |  | H:G0.DIRECTIVE.DATA_NOSNIPPET |
| everlane | ecommerce | 2 | 120 |  |  |  |  |  |  | (no report) + 119.43s    - F-005 (G2.SD.MARKUP_TEXT_CONTRADICTION) evidence_refs[0] jsonpath: path '$.hasVariant[6].offers.availability' does not yield 'OutOfStock' |
| chumbak | ecommerce | 0 | 136 | 30 | ecommerce(medium) | 0/2/13/13 | 0 | 3 |  | H:G2.SD.MARKUP_TEXT_CONTRADICTION; H:G2.SD.REQUIRED_PROP_MISSING |
| freshworks | saas_software | 2 | 134 |  |  |  |  |  |  | (no report) + 133.65s    - F-008 (G3.ENTITY.WEAK_DISAMBIGUATION) evidence_refs[1] jsonpath: path '$.@graph[0].author.name' does not yield '' |
| zoho | saas_software | 0 | 61 | 30 | ambiguous(low) | 0/0/13/12 | 0 | 3 |  |  |
| figma | saas_software | 0 | 77 | 30 | ecommerce(low) | 0/2/11/13 | 1 | 5 |  | H:G0.ROBOTS.AI_AGENT_BLOCKED; H:G0.ROBOTS.AI_AGENT_BLOCKED |
| postman | saas_software | 0 | 87 | 30 | saas_software(low) | 0/0/6/4 | 0 | 5 |  |  |
| calendly | saas_software | 0 | 139 | 30 | ambiguous(low) | 0/1/7/12 | 1 | 4 |  | H:G0.DIRECTIVE.NOINDEX_META |
| razorpay | saas_software | 0 | 172 | 30 | publisher(medium) | 0/1/9/11 | 0 | 5 |  | H:G0.SITEMAP.INVALID_XML |
| djangodocs | documentation | 0 | 58 | 30 | docs_knowledge(high) | 0/0/8/8 | 0 | 3 |  |  |
| nextjsdocs | documentation | 0 | 133 | 30 | docs_knowledge(high) | 0/0/7/3 | 0 | 4 |  |  |
| k8sdocs | documentation | 2 | 119 |  |  |  |  |  |  | (no report) + 118.66s    - F-013 (G3.ENTITY.WEAK_DISAMBIGUATION) evidence_refs[1] jsonpath: path '$.name' does not yield '' |
| githubdocs | documentation | 2 | 50 |  |  |  |  |  |  | (no report) +  49.43s    - F-007 (G3.ENTITY.WEAK_DISAMBIGUATION) evidence_refs[1] jsonpath: path '$[0].name' does not yield '' |
| thehindu | publisher | 0 | 64 | 30 | publisher(high) | 1/2/8/12 | 0 | 3 |  | C:G0.ROBOTS.AI_AGENT_BLOCKED; H:G0.SITEMAP.INVALID_XML; H:GE.FRICTION.CONTENT_GATING_OVERLAY |
| indianexpress | publisher | 0 | 143 | 30 | publisher(high) | 1/2/5/8 | 1 | 3 |  | C:G0.ROBOTS.AI_AGENT_BLOCKED; H:G0.DIRECTIVE.NOINDEX_META; H:G2.SD.REQUIRED_PROP_MISSING |
| wired | publisher | 0 | 131 | 30 | publisher(high) | 3/2/11/13 | 1 | 3 |  | C:G0.DIRECTIVE.DATA_NOSNIPPET; C:G0.DIRECTIVE.NOINDEX_META; C:G0.ROBOTS.AI_AGENT_BLOCKED; H:G2.SD.REQUIRED_PROP_MISSING; H:GE.FRICTION.CONTENT_GATING_OVERLAY |
| spiegel | publisher | 0 | 114 | 29 | ecommerce(medium) | 0/2/10/13 | 1 | 4 |  | H:G0.DIRECTIVE.NOARCHIVE_NOCACHE; H:G0.DIRECTIVE.NOINDEX_META |
| nytimes | publisher | 0 | 69 | 8 | publisher(high) | 0/1/4/5 | 12 | 6 |  | H:G0.GATING.NON_BROWSER_REFUSED |
| theobroma | local_business | 0 | 136 | 30 | ambiguous(low) | 0/0/5/2 | 0 | 2 |  |  |
| shakeshack | local_business | 0 | 9 | 0 | marketing_brochure(low) | 0/1/0/0 | 1 | 4 |  | H:G0.GATING.NON_BROWSER_REFUSED |
| chaayos | local_business | 0 | 47 | 30 | ecommerce(low) | 0/1/9/9 | 0 | 2 |  | H:G2.SD.ABSENT |
| indiagov | government | 0 | 13 | 0 | marketing_brochure(low) | 0/1/0/0 | 1 | 4 |  | H:G0.GATING.NON_BROWSER_REFUSED |
| usagov | government | 2 | 196 |  |  |  |  |  |  | (no report) + 196.32s    - F-008 (G3.ENTITY.WEAK_DISAMBIGUATION) evidence_refs[1] jsonpath: path '$.name' does not yield '' |
| mit | education | 0 | 81 | 30 | publisher(high) | 1/2/8/5 | 0 | 4 |  | C:G0.DIRECTIVE.DATA_NOSNIPPET; H:G0.SITEMAP.MISSING; H:G1.NONTEXT.VIDEO_WITHOUT_TRANSCRIPT |
| iitm | education | 0 | 45 | 30 | ambiguous(low) | 0/1/8/10 | 0 | 2 |  | H:G0.SITEMAP.INVALID_XML |
| rakuten | marketplace | 2 | 278 |  |  |  |  |  |  | (no report) + 277.42s    - F-012 (G3.ENTITY.AMBIGUITY_RISK) evidence_refs[1] jsonpath: path '$.name' does not yield '' |
| elpais | publisher | 0 | 114 | 30 | publisher(low) | 1/2/6/14 | 0 | 3 |  | C:G0.ROBOTS.AI_AGENT_BLOCKED; H:G0.DIRECTIVE.NOARCHIVE_NOCACHE; H:G0.SITEMAP.MISSING |
| tldraw | web_app | 0 | 4 | 1 | marketing_brochure(low) | 0/2/2/1 | 0 | 3 |  | H:G0.ROBOTS.AUDIT_PATH_DISALLOWED; H:GE.NAV.NAV_ONLY_IN_JS |
| squoosh | web_app | 0 | 13 | 1 | marketing_brochure(low) | 0/1/2/3 | 0 | 3 |  | H:G0.SITEMAP.MISSING |
| zomato | marketplace | 4 | 11 |  |  |  |  |  |  | (no report) +  10.38s  www.zomato.com did not respond (timed out after 10s: The read operation timed out); target unreachable |
| swiggy | marketplace | 0 | 124 | 30 | marketplace_directory(high) | 0/1/5/10 | 0 | 4 |  | H:G0.SITEMAP.STALE_OR_TRUNCATED |
| cern | tiny | 2 | 54 |  |  |  |  |  |  | (no report) +  53.56s    - F-011 (GE.FRICTION.NO_VIEWPORT_META) evidence_refs[1] dom: selector 'head' matches nothing in the stored response |
| paulgraham | tiny | 0 | 23 | 6 | saas_software(low) | 0/2/6/5 | 0 | 7 |  | H:G0.SITEMAP.MISSING; H:GE.FRICTION.NO_VIEWPORT_META |
| reuters | publisher | 0 | 66 | 22 | publisher(high) | 0/3/14/5 | 0 | 6 |  | H:G0.DIRECTIVE.NOINDEX_META; H:G0.ROBOTS.AI_AGENT_BLOCKED; H:G0.SITEMAP.INVALID_XML |
| medium | publisher | 0 | 31 | 0 | marketing_brochure(low) | 0/2/0/0 | 0 | 4 |  | H:G0.GATING.NON_BROWSER_REFUSED; H:G0.ROBOTS.AI_AGENT_BLOCKED |
| airbnb_in | marketplace | 0 | 85 | 30 | ecommerce(medium) | 2/2/8/8 | 1 | 4 |  | C:G0.DIRECTIVE.DATA_NOSNIPPET; C:G0.DIRECTIVE.NOINDEX_META; H:G0.ROBOTS.AI_AGENT_BLOCKED; H:G2.SD.REQUIRED_PROP_MISSING |
| makemytrip | marketplace | 4 | 11 |  |  |  |  |  |  | (no report) +  10.28s  www.makemytrip.com did not respond (timed out after 10s: The read operation timed out); target unreachable |
| hubspot | saas_software | 0 | 84 | 30 | ambiguous(low) | 0/0/9/18 | 0 | 3 |  |  |
| v_bare | variant | 0 | 133 | 30 | ambiguous(low) | 0/0/8/18 | 0 | 2 |  |  |
| v_http | variant | 0 | 39 | 4 | publisher(low) | 1/1/7/8 | 0 | 3 |  | C:G0.DIRECTIVE.DATA_NOSNIPPET; H:G0.SITEMAP.MISSING |
| v_upper | variant | 0 | 1036 | 30 | ambiguous(low) | 1/2/8/9 | 0 | 5 |  | C:G0.DIRECTIVE.DATA_NOSNIPPET; H:G0.DIRECTIVE.NOINDEX_META; H:G2.SD.MARKUP_TEXT_CONTRADICTION |
| v_deep | variant | 0 | 64 | 30 | ambiguous(low) | 0/0/13/12 | 0 | 3 |  |  |
| v_query | variant | 0 | 1024 | 30 | ambiguous(low) | 1/1/6/17 | 0 | 3 |  | C:G0.DIRECTIVE.DATA_NOSNIPPET; H:G2.SD.MARKUP_TEXT_CONTRADICTION |

## Check fire rates (reported findings)

| Check | Sites | Critical | High | Medium | Low |
| :-- | :-: | :-: | :-: | :-: | :-: |
| `G3.CORROB.NO_CITABLE_PRIMARY_SOURCE` | 23 | 0 | 0 | 23 | 0 |
| `G3.ENTITY.WEAK_DISAMBIGUATION` | 22 | 0 | 0 | 0 | 22 |
| `G1.TEXT.BOILERPLATE_DOMINANT` | 21 | 0 | 0 | 13 | 8 |
| `GE.ORIENT.NO_PRIMARY_CTA` | 20 | 0 | 0 | 5 | 15 |
| `GE.ORIENT.NO_H1` | 20 | 0 | 0 | 3 | 17 |
| `G3.ANSW.NO_DEFINITIONAL_SENTENCE` | 19 | 0 | 0 | 19 | 0 |
| `G2.SD.ENTITY_GRAPH_UNLINKED` | 18 | 0 | 0 | 18 | 0 |
| `G2.SD.ABSENT` | 18 | 0 | 1 | 17 | 0 |
| `GE.NAV.DEAD_END_PAGE` | 16 | 0 | 0 | 5 | 11 |
| `GE.FRICTION.CLS_RISK` | 16 | 0 | 0 | 11 | 5 |
| `G3.ENTITY.NAME_INCONSISTENCY` | 15 | 0 | 0 | 15 | 0 |
| `GE.FRICTION.NO_SKIP_LINK` | 15 | 0 | 0 | 0 | 15 |
| `GE.ORIENT.HEADING_HIERARCHY_SKIP` | 15 | 0 | 0 | 0 | 15 |
| `G1.NONTEXT.VIDEO_WITHOUT_TRANSCRIPT` | 14 | 0 | 1 | 8 | 5 |
| `G2.SD.REQUIRED_PROP_MISSING` | 14 | 0 | 6 | 4 | 4 |
| `G3.ANSW.NO_ANCHOR_ADDRESSABILITY` | 14 | 0 | 0 | 0 | 14 |
| `GE.FRICTION.UNLABELLED_FORM_FIELDS` | 14 | 0 | 0 | 6 | 8 |
| `GE.NAV.ORPHAN_PAGES` | 14 | 0 | 0 | 7 | 7 |
| `G3.CORROB.NO_PRESS_KIT` | 13 | 0 | 0 | 0 | 13 |
| `GE.FRICTION.LOW_CONTRAST_DECLARED` | 13 | 0 | 0 | 6 | 7 |
| `G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES` | 12 | 0 | 1 | 3 | 8 |
| `G3.CORROB.NO_AUTHOR_IDENTITY` | 12 | 0 | 0 | 11 | 1 |
| `G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE` | 12 | 0 | 0 | 5 | 7 |
| `GE.ORIENT.NO_VALUE_PROP` | 12 | 0 | 0 | 0 | 12 |
| `G2.SD.BREADCRUMB_ABSENT_ON_DEEP_PAGES` | 10 | 0 | 0 | 0 | 10 |
| `GE.ORIENT.TITLE_MISMATCH` | 10 | 0 | 0 | 3 | 7 |
| `G1.TEXT.MAIN_LANDMARK_MISSING` | 10 | 0 | 0 | 5 | 5 |
| `GE.NAV.NO_SITE_SEARCH` | 10 | 0 | 0 | 10 | 0 |
| `G1.NONTEXT.ALT_NON_DESCRIPTIVE` | 9 | 0 | 0 | 1 | 8 |
| `GE.FRICTION.TAB_TRAP_CONTENT` | 9 | 0 | 0 | 3 | 6 |
| `GE.NAV.NO_RELATED_CONTENT` | 9 | 0 | 0 | 0 | 9 |
| `G0.DIRECTIVE.NOINDEX_META` | 9 | 2 | 5 | 2 | 0 |
| `GE.FRICTION.HEAVY_PAGE` | 9 | 0 | 0 | 0 | 9 |
| `GE.READ.NO_LISTS` | 9 | 0 | 0 | 0 | 9 |
| `G0.ROBOTS.AI_AGENT_BLOCKED` | 9 | 4 | 5 | 0 | 0 |
| `G0.DIRECTIVE.DATA_NOSNIPPET` | 8 | 6 | 1 | 1 | 0 |
| `G2.SD.RECOMMENDED_PROP_MISSING` | 7 | 0 | 0 | 7 | 0 |
| `G2.SD.SEARCHACTION_MISSING` | 7 | 0 | 0 | 0 | 7 |
| `G3.ENTITY.AMBIGUITY_RISK` | 6 | 0 | 0 | 6 | 0 |
| `G2.SD.FAQ_MARKUP_MISMATCH` | 6 | 0 | 0 | 4 | 2 |
| `G3.FRESH.DATE_CONTRADICTION` | 5 | 0 | 0 | 1 | 4 |
| `GE.FRICTION.LAYOUT_SHIFT_FONTS` | 5 | 0 | 0 | 0 | 5 |
| `GE.ORIENT.MULTIPLE_H1` | 5 | 0 | 0 | 0 | 5 |
| `G0.HTTP.REDIRECT_CHAIN` | 5 | 0 | 0 | 5 | 0 |
| `G2.SD.MARKUP_TEXT_CONTRADICTION` | 5 | 0 | 3 | 2 | 0 |
| `G3.ANSW.KEY_FACTS_UNLISTED` | 5 | 0 | 0 | 1 | 4 |
| `G0.SITEMAP.NOT_IN_ROBOTS` | 5 | 0 | 0 | 5 | 0 |
| `G0.SITEMAP.MISSING` | 5 | 0 | 5 | 0 | 0 |
| `G2.SD.TYPE_MISMATCH` | 4 | 0 | 0 | 2 | 2 |
| `GE.ORIENT.GENERIC_H1` | 4 | 0 | 0 | 0 | 4 |
| `GE.ORIENT.DEEP_PAGE_NO_CONTEXT` | 4 | 0 | 0 | 0 | 4 |
| `GE.FRICTION.CONTENT_GATING_OVERLAY` | 4 | 0 | 2 | 2 | 0 |
| `G0.SITEMAP.INVALID_XML` | 4 | 0 | 4 | 0 | 0 |
| `G2.CANONICAL.NON_SELF_REFERENTIAL` | 4 | 0 | 0 | 3 | 1 |
| `G2.CANONICAL.PARAM_DUPLICATES` | 4 | 0 | 0 | 4 | 0 |
| `G0.DIRECTIVE.NOARCHIVE_NOCACHE` | 4 | 0 | 2 | 2 | 0 |
| `G0.GATING.NON_BROWSER_REFUSED` | 4 | 0 | 4 | 0 | 0 |
| `G2.CANONICAL.MISSING` | 3 | 0 | 0 | 3 | 0 |
| `G0.SITEMAP.URLS_BROKEN` | 3 | 0 | 0 | 1 | 2 |
| `G1.NONTEXT.FACT_LOCKED_IN_MEDIA` | 3 | 0 | 0 | 1 | 2 |
| `G2.CANONICAL.VS_OGURL_CONFLICT` | 3 | 0 | 0 | 0 | 3 |
| `G0.ROBOTS.AUDIT_PATH_DISALLOWED` | 3 | 0 | 1 | 2 | 0 |
| `G2.SD.JSONLD_SYNTAX_ERROR` | 2 | 0 | 0 | 2 | 0 |
| `G0.SITEMAP.STALE_OR_TRUNCATED` | 2 | 0 | 2 | 0 | 0 |
| `GE.FRICTION.RENDER_BLOCKING_HEAD` | 2 | 0 | 0 | 1 | 1 |
| `G0.SITEMAP.NO_MACHINE_FEED` | 2 | 0 | 0 | 2 | 0 |
| `G0.GATING.BOT_STATUS_DIVERGENCE` | 2 | 0 | 0 | 2 | 0 |
| `GE.NAV.BROKEN_INTERNAL_LINKS` | 2 | 0 | 0 | 1 | 1 |
| `G3.FRESH.STALE_CONTENT_MARKERS` | 1 | 0 | 0 | 1 | 0 |
| `G2.SD.PROP_TYPE_INVALID` | 1 | 0 | 0 | 0 | 1 |
| `G3.ENTITY.SAMEAS_BROKEN` | 1 | 0 | 0 | 1 | 0 |
| `G0.DIRECTIVE.NOSNIPPET` | 1 | 0 | 0 | 1 | 0 |
| `G3.ANSW.PRONOUN_ONLY_ENTITY_REFERENCE` | 1 | 0 | 0 | 1 | 0 |
| `GE.FRICTION.INTRUSIVE_STICKY` | 1 | 0 | 0 | 1 | 0 |
| `GE.NAV.NAV_ONLY_IN_JS` | 1 | 0 | 1 | 0 | 0 |
| `G3.ANSW.NO_QUESTION_SHAPED_HEADINGS` | 1 | 0 | 0 | 1 | 0 |
| `GE.READ.LONG_SENTENCES` | 1 | 0 | 0 | 0 | 1 |
| `GE.FRICTION.NO_VIEWPORT_META` | 1 | 0 | 1 | 0 | 0 |
| `G1.NONTEXT.IFRAME_LOCKED_CONTENT` | 1 | 0 | 0 | 1 | 0 |
| `G2.CANONICAL.HREFLANG_ASYMMETRIC` | 1 | 0 | 0 | 1 | 0 |
