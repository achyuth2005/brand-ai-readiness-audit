"""The false-positive rate, made executable.

Every test here builds a page that is *correct* but superficially resembles a defect, and
asserts the check that guards against it stays silent. A regression here is a build
break, not a warning: on unseen sites a noisy report damages detection accuracy, output
design and generalization at once.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import helpers
from fixtures import sites


class NegativeControlTests(unittest.TestCase):

    def _results(self, specs, skills=helpers.SPECIALISTS):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = helpers.build_ledger(Path(tmp) / "ledger", specs)
            results = {skill: helpers.run_skill(skill, ledger, Path(tmp)) for skill in skills}
        self.assertEqual(helpers.analyzer_errors(results), [])
        return results

    def assertSilent(self, results, check_id, observations_too=True):
        found = helpers.all_ids(results)
        if observations_too:
            found |= helpers.all_ids(results, "observations")
        self.assertNotIn(check_id, found)

    # -- G0 --------------------------------------------------------------------------------

    def test_noindex_on_search_pages_is_correct(self):
        results = self._results(sites.utility_noindex(), ["access-policy-audit"])
        self.assertSilent(results, "G0.DIRECTIVE.NOINDEX_META")

    def test_disallowing_only_utility_paths_blocks_no_crawler(self):
        results = self._results(sites.cart_only_disallow(), ["access-policy-audit"])
        self.assertSilent(results, "G0.ROBOTS.AI_AGENT_BLOCKED")

    def test_training_only_block_is_not_a_citation_blocker(self):
        results = self._results(sites.training_only_block(), ["access-policy-audit"])
        self.assertNotIn("G0.ROBOTS.AI_AGENT_BLOCKED", helpers.all_ids(results))
        self.assertIn("G0.ROBOTS.TRAINING_AGENT_BLOCKED",
                      helpers.all_ids(results, "observations"))

    def test_offsite_profiles_refused_by_their_own_robots_are_not_site_policy(self):
        results = self._results(sites.offsite_profiles_disallowed(), ["access-policy-audit"])
        self.assertSilent(results, "G0.ROBOTS.AUDIT_PATH_DISALLOWED")

    def test_index_alias_canonicalising_to_its_clean_url_is_correct(self):
        results = self._results(sites.locale_index_aliases(), ["structured-data-audit"])
        self.assertSilent(results, "G2.CANONICAL.NON_SELF_REFERENTIAL")

    def test_rdfa_breadcrumb_items_are_recognised_with_or_without_extruct(self):
        results = self._results(sites.rdfa_breadcrumbs(), ["structured-data-audit"])
        breadcrumb_findings = [f for data in results.values() for f in data["findings"]
                               if f["check_id"] == "G2.SD.REQUIRED_PROP_MISSING"
                               and "BreadcrumbList" in f["title"]]
        self.assertEqual(breadcrumb_findings, [])

    def test_nested_organisation_reference_needs_only_a_name(self):
        results = self._results(sites.nested_author_organisation(), ["structured-data-audit"])
        organisation_findings = [f for data in results.values() for f in data["findings"]
                                 if f["check_id"] == "G2.SD.REQUIRED_PROP_MISSING"
                                 and "Organization" in f["title"]]
        self.assertEqual(organisation_findings, [])

    def test_site_wide_secondary_interpretation_defect_is_medium(self):
        from auditkit import severity
        self.assertEqual(severity.derive("G2", "site", "secondary", "high")[0], "medium")
        self.assertEqual(severity.derive("G2", "template", "primary", "high")[0], "high")

    def test_quality_gates_never_reach_critical(self):
        from auditkit import severity
        for gate in ("G2", "G3"):
            self.assertEqual(severity.derive(gate, "site", "primary", "high")[0], "high")
        self.assertEqual(severity.derive("G0", "site", "primary", "high")[0], "critical")

    def test_software_plan_offer_needs_no_stock_status(self):
        results = self._results(sites.software_plan_offers(), ["structured-data-audit"])
        offer_findings = [f for data in results.values() for f in data["findings"]
                          if f["check_id"] == "G2.SD.REQUIRED_PROP_MISSING"
                          and "availability" in f["title"]]
        self.assertEqual(offer_findings, [])

    def test_pages_under_a_routing_prefix_have_no_parent_to_link(self):
        results = self._results(sites.routing_prefix_pages(), ["engagement-audit"])
        self.assertSilent(results, "GE.ORIENT.DEEP_PAGE_NO_CONTEXT")

    def test_about_page_linked_on_a_sister_host_is_not_reported_missing(self):
        results = self._results(sites.about_page_on_sister_host(), ["entity-trust-audit"])
        self.assertNotIn("G3.CORROB.NO_CITABLE_PRIMARY_SOURCE", helpers.all_ids(results))

    def test_brand_that_gives_its_customers_something_is_defined(self):
        head = '<meta property="og:site_name" content="Widgets">'
        home = sites.html("Widgets", "<h1>Your tools, your rules</h1><p>Widgets gives "
                                     "workshop teams one place to plan, track and ship "
                                     "their builds.</p>", head=head)
        specs = [sites.page("/", home, depth=0),
                 sites.page("/tools", sites.html("Tools | Widgets", "<h1>Tools</h1>"
                                                  "<p>Tools.</p>", head=head))]
        results = self._results(specs, ["answerability-audit"])
        self.assertSilent(results, "G3.ANSW.NO_DEFINITIONAL_SENTENCE")

    def test_section_fronts_of_links_need_not_name_the_brand(self):
        results = self._results(sites.section_index_pages(), ["answerability-audit"])
        self.assertSilent(results, "G3.ANSW.PRONOUN_ONLY_ENTITY_REFERENCE")

    def test_truncated_sitemap_is_not_invalid_xml(self):
        results = self._results(sites.truncated_sitemap(), ["access-policy-audit"])
        self.assertSilent(results, "G0.SITEMAP.INVALID_XML")

    def test_section_absent_from_a_partly_read_sitemap_index_is_not_claimed(self):
        results = self._results(sites.partially_read_sitemap_index(), ["access-policy-audit"])
        self.assertSilent(results, "G0.SITEMAP.STALE_OR_TRUNCATED")

    def test_refused_sitemap_urls_are_not_broken(self):
        results = self._results(sites.refused_sitemap_urls(), ["access-policy-audit"])
        self.assertSilent(results, "G0.SITEMAP.URLS_BROKEN")

    def test_refused_declared_sitemap_is_reported_as_refused(self):
        results = self._results(sites.refused_declared_sitemap(), ["access-policy-audit"])
        titles = [f["title"] for data in results.values() for f in data["findings"]
                  if f["check_id"] == "G0.SITEMAP.MISSING"]
        self.assertEqual(len(titles), 1)
        self.assertIn("refuse", titles[0])

    def test_one_refused_page_among_served_pages_is_not_an_edge_block(self):
        results = self._results(sites.one_refused_page(), ["access-policy-audit"])
        self.assertSilent(results, "G0.GATING.NON_BROWSER_REFUSED")

    def test_refusal_without_a_second_signal_is_only_an_observation(self):
        results = self._results(sites.bare_refusal(), ["access-policy-audit"])
        self.assertNotIn("G0.GATING.NON_BROWSER_REFUSED", helpers.all_ids(results))
        self.assertIn("G0.GATING.NON_BROWSER_REFUSED",
                      helpers.all_ids(results, "observations"))

    def test_common_crawl_block_is_a_training_decision_not_a_citation_blocker(self):
        specs = [sites.robots("User-agent: CCBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"),
                 sites.page("/", sites.html("Home", "<h1>Tools</h1><p>Tools for makers.</p>"))]
        results = self._results(specs, ["access-policy-audit"])
        self.assertNotIn("G0.ROBOTS.AI_AGENT_BLOCKED", helpers.all_ids(results))
        self.assertIn("G0.ROBOTS.TRAINING_AGENT_BLOCKED",
                      helpers.all_ids(results, "observations"))

    def test_an_inferred_edge_refusal_is_never_critical(self):
        from auditkit import severity
        self.assertEqual(severity.derive("G0", "site", "primary", "medium",
                                         "G0.GATING.NON_BROWSER_REFUSED")[0], "high")

    # -- G1 --------------------------------------------------------------------------------

    def test_server_rendered_framework_page_is_not_client_rendered(self):
        results = self._results(sites.ssr_with_next_data(), ["render-extraction-audit"])
        self.assertSilent(results, "G1.RENDER.CSR_CONTENT_GAP")

    def test_payload_text_a_server_rendered_page_never_shows_is_not_a_render_gap(self):
        results = self._results(sites.ssr_with_unrendered_payload_text(),
                                ["render-extraction-audit"])
        self.assertSilent(results, "G1.RENDER.CSR_CONTENT_GAP")

    def test_short_contact_page_is_not_a_render_gap(self):
        results = self._results(sites.short_contact_page(), ["render-extraction-audit"])
        self.assertSilent(results, "G1.RENDER.CSR_CONTENT_GAP")

    def test_decorative_images_with_empty_alt_are_correct(self):
        results = self._results(sites.decorative_images(), ["render-extraction-audit"])
        self.assertSilent(results, "G1.NONTEXT.ALT_MISSING_ON_CONTENT_IMAGES")
        self.assertSilent(results, "G1.NONTEXT.ALT_NON_DESCRIPTIVE")

    # -- G2 --------------------------------------------------------------------------------

    def test_price_variants_are_not_a_contradiction(self):
        results = self._results(sites.price_variants(), ["structured-data-audit"])
        self.assertSilent(results, "G2.SD.MARKUP_TEXT_CONTRADICTION")

    def test_comma_decimal_currency_is_not_a_contradiction(self):
        results = self._results(sites.localised_currency(), ["structured-data-audit"])
        self.assertSilent(results, "G2.SD.MARKUP_TEXT_CONTRADICTION")

    def test_retailers_named_as_sellers_are_not_conflicting_publishers(self):
        results = self._results(sites.retailer_offers(), ["structured-data-audit"])
        self.assertSilent(results, "G2.SD.DUPLICATE_CONFLICTING_ENTITIES")

    def test_year_only_founding_date_is_valid(self):
        org = {"@context": "https://schema.org", "@type": "Organization", "name": "Widgets",
               "url": f"{sites.SITE}/", "foundingDate": "2011"}
        specs = [sites.page(path, sites.html("Widgets", "<h1>Widgets</h1><p>Page.</p>",
                                             head=sites.ld(org)))
                 for path in ("/", "/a", "/b")]
        results = self._results(specs, ["structured-data-audit"])
        self.assertSilent(results, "G2.SD.PROP_TYPE_INVALID")

    def test_ungrouped_thousands_parse_as_whole_numbers(self):
        from auditkit import textnorm
        cases = (("1234.56", 1234.56), ("1299", 1299.0), ("1.234,56 \u20ac", 1234.56),
                 ("\u00a31,499.00", 1499.0), ("49.00", 49.0), ("12 345,00 EUR", 12345.0))
        for raw, amount in cases:
            self.assertAlmostEqual(textnorm.parse_money(raw).amount, amount, places=2, msg=raw)

    # -- G3 --------------------------------------------------------------------------------

    def test_long_and_short_forms_of_one_name_are_consistent(self):
        results = self._results(sites.long_short_names(), ["entity-trust-audit"])
        self.assertSilent(results, "G3.ENTITY.NAME_INCONSISTENCY")

    def test_facts_told_as_a_story_count_as_canonical_facts(self):
        results = self._results(sites.about_page_story_facts(), ["entity-trust-audit"])
        self.assertSilent(results, "G3.CORROB.NO_CITABLE_PRIMARY_SOURCE")

    def test_about_page_in_an_unsupported_language_is_not_judged(self):
        results = self._results(sites.foreign_language_about_page(), ["entity-trust-audit"])
        self.assertNotIn("G3.CORROB.NO_CITABLE_PRIMARY_SOURCE", helpers.all_ids(results))

    def test_link_index_pages_are_not_boilerplate_dominated(self):
        results = self._results(sites.link_index_pages(), ["render-extraction-audit"])
        self.assertSilent(results, "G1.TEXT.BOILERPLATE_DOMINANT")

    def test_subscription_named_by_is_part_of_is_not_a_product_listing(self):
        results = self._results(sites.subscription_part_of(), ["structured-data-audit"])
        product_findings = [f for data in results.values() for f in data["findings"]
                            if f["check_id"] == "G2.SD.REQUIRED_PROP_MISSING"
                            and "Product" in f["title"]]
        self.assertEqual(product_findings, [])

    def test_old_versions_consolidated_onto_the_current_copy_are_correct(self):
        results = self._results(sites.versioned_docs_canonical(), ["structured-data-audit"])
        self.assertSilent(results, "G2.CANONICAL.NON_SELF_REFERENTIAL")

    def test_weak_posture_without_a_colliding_name_is_not_ambiguity_risk(self):
        head = '<meta property="og:site_name" content="Northwind Instruments">'
        specs = [sites.page(path, sites.html(f"{title} | Northwind Instruments",
                                             f"<h1>{title}</h1><p>Page.</p>", head=head),
                            **({"depth": 0} if path == "/" else {}))
                 for path, title in (("/", "Home"), ("/tools", "Tools"), ("/guides", "Guides"))]
        results = self._results(specs, ["entity-trust-audit"])
        self.assertNotIn("G3.ENTITY.AMBIGUITY_RISK", helpers.all_ids(results))
        self.assertIn("G3.ENTITY.WEAK_DISAMBIGUATION", helpers.all_ids(results))

    def test_path_words_that_end_documentation_urls_are_not_products(self):
        from auditkit import urlnorm
        api = urlnorm.normalise("https://site.test/docs/api/CSSRuleList/item")
        self.assertNotIn("product", urlnorm.path_classes(api))
        listing = urlnorm.normalise("https://site.test/items/bench-vise")
        self.assertIn("product", urlnorm.path_classes(listing))
        self.assertFalse(urlnorm.is_utility(api), "documentation about an API is content")
        self.assertTrue(urlnorm.is_utility(urlnorm.normalise("https://site.test/api/v1/items")))

    def test_quick_view_popups_are_utility_pages(self):
        from auditkit import urlnorm
        self.assertTrue(urlnorm.is_utility(
            urlnorm.normalise("https://site.test/Quicklook.aspx?ProductID=B-1")))

    def test_tagline_in_the_site_name_is_not_part_of_the_brand(self):
        results = self._results(sites.tagline_brand_name(), ["answerability-audit"])
        self.assertSilent(results, "G3.ANSW.NO_DEFINITIONAL_SENTENCE")

    def test_facts_stated_in_narrative_prose_count_as_canonical_facts(self):
        results = self._results(sites.narrative_about_page(), ["entity-trust-audit"])
        self.assertSilent(results, "G3.CORROB.NO_CITABLE_PRIMARY_SOURCE")

    # -- GE --------------------------------------------------------------------------------

    def test_correctly_hidden_modal_is_not_an_overlay(self):
        results = self._results(sites.hidden_modal(), ["engagement-audit"])
        self.assertSilent(results, "GE.FRICTION.CONTENT_GATING_OVERLAY")

    def test_backdrop_revealed_only_by_an_absent_state_is_not_an_overlay(self):
        results = self._results(sites.state_revealed_backdrop(), ["engagement-audit"])
        self.assertSilent(results, "GE.FRICTION.CONTENT_GATING_OVERLAY")

    def test_client_rendered_shells_are_not_judged_on_orientation(self):
        results = self._results(sites.client_rendered_shells(), ["engagement-audit"])
        for check_id in ("GE.ORIENT.NO_H1", "GE.ORIENT.NO_PRIMARY_CTA",
                         "GE.ORIENT.DEEP_PAGE_NO_CONTEXT", "GE.NAV.DEAD_END_PAGE"):
            self.assertSilent(results, check_id)

    # -- the whole package -----------------------------------------------------------------

    def test_prompt_injection_in_page_content_is_treated_as_data(self):
        results = self._results(sites.prompt_injection())
        self.assertTrue(all(r["_exit"] == 0 for r in results.values()))
        self.assertIn("GE.FRICTION.NO_VIEWPORT_META",
                      helpers.check_ids(results["engagement-audit"]),
                      "the page's real defect must still be reported despite the injected "
                      "instructions")

    def test_well_built_site_produces_zero_findings(self):
        results = self._results(sites.well_built_site())
        fired = {skill: sorted(helpers.check_ids(data)) for skill, data in results.items()
                 if data["findings"]}
        self.assertEqual(fired, {}, "a well-built site must not produce findings")


class EdgeRefusalTests(unittest.TestCase):
    """A site that refuses non-browser clients gets a report, not an exit code."""

    def test_refusal_is_reported_and_its_consequences_are_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = helpers.build_ledger(Path(tmp) / "ledger", sites.edge_refusal())
            code, report = helpers.run_offline_audit(ledger, Path(tmp) / "out")
        self.assertEqual(code, 0)
        active = {f["check_id"] for f in report["findings"]}
        self.assertIn("G0.GATING.NON_BROWSER_REFUSED", active)
        consequences = {c for c in active if c.startswith(("G0.SITEMAP.", "G1.", "G2.", "G3."))}
        self.assertEqual(consequences, set())


class ArrivalStateCssTests(unittest.TestCase):
    """Static CSS is judged as it applies on arrival, not under states nobody triggered."""

    def _node(self, markup, selector):
        from auditkit.dom import Document
        doc = Document(markup)
        return doc, doc.soup.select_one(selector)

    def test_state_rule_does_not_reveal_a_hidden_node(self):
        from auditkit import cssutil
        sheet = cssutil.parse([".panel{visibility:hidden}"
                               ".menu--open .panel{visibility:visible}"])
        _doc, node = self._node('<div class="menu"><div class="panel"></div></div>', ".panel")
        declarations = sheet.declarations_for(tag="div", classes=["panel"], node=node)
        self.assertTrue(cssutil.is_hidden_by_css(declarations)[0])

    def test_state_rule_applies_when_its_state_is_present(self):
        from auditkit import cssutil
        sheet = cssutil.parse([".panel{visibility:hidden}"
                               ".menu--open .panel{visibility:visible}"])
        _doc, node = self._node('<div class="menu menu--open"><div class="panel"></div></div>',
                                ".panel")
        declarations = sheet.declarations_for(tag="div", classes=["panel"], node=node)
        self.assertFalse(cssutil.is_hidden_by_css(declarations)[0])

    def test_pseudo_element_geometry_is_not_the_node_geometry(self):
        from auditkit import cssutil
        sheet = cssutil.parse([".card::before{position:fixed;inset:0;z-index:999}"])
        _doc, node = self._node('<div class="card"></div>', ".card")
        declarations = sheet.declarations_for(tag="div", classes=["card"], node=node)
        self.assertFalse(cssutil.is_viewport_covering(declarations)[0])

    def test_conditional_scroll_lock_counts_only_when_its_condition_holds(self):
        from auditkit import cssutil
        sheet = cssutil.parse(["body:has(.search-dialog){overflow:hidden}"
                               "body.modal-open{overflow:hidden}"])
        idle, _ = self._node("<html><body><main></main></body></html>", "main")
        self.assertFalse(sheet.body_scroll_locked(idle.soup)[0])
        opened, _ = self._node('<html><body class="modal-open"><main></main></body></html>',
                               "main")
        self.assertTrue(sheet.body_scroll_locked(opened.soup)[0])
        self.assertTrue(cssutil.parse(["body{overflow:hidden}"]).body_scroll_locked()[0])


if __name__ == "__main__":
    unittest.main()
