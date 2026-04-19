"""
Comprehensive tests for tvdb_lookup.py.

Tests all offline logic: parsing, cleaning, matching, and high-level features
with mock API data representing real Dinosaur Train and Paw Patrol episodes.
"""

import os
import unittest
from unittest.mock import patch, MagicMock

import tvdb_lookup as tv

# ═══════════════════════════════════════════════════════════════════════════
# Test data: real TVDB episode records (subset)
# ═══════════════════════════════════════════════════════════════════════════

DINOSAUR_TRAIN_EPISODES = [
    {'seasonNumber': 1, 'number': 7, 'name': "I'm a T. Rex!", 'aired': '2009-09-07'},
    {'seasonNumber': 1, 'number': 8, 'name': 'Ned The Quadruped', 'aired': '2009-09-07'},
    {'seasonNumber': 1, 'number': 9, 'name': 'One Smart Dinosaur', 'aired': '2009-09-11'},
    {'seasonNumber': 1, 'number': 10, 'name': 'Petey The Peteinosaurus', 'aired': '2009-09-11'},
    {'seasonNumber': 1, 'number': 11, 'name': 'Fast Friends', 'aired': '2009-09-17'},
    {'seasonNumber': 1, 'number': 12, 'name': 'T. Rex Teeth', 'aired': '2009-09-17'},
    {'seasonNumber': 1, 'number': 13, 'name': 'Now With Feathers!', 'aired': '2009-09-18'},
    {'seasonNumber': 1, 'number': 14, 'name': 'A Frill a Minute', 'aired': '2009-09-18'},
    {'seasonNumber': 1, 'number': 19, 'name': 'Laura The Giganotosaurus', 'aired': '2009-09-29'},
    {'seasonNumber': 1, 'number': 20, 'name': 'Dinosaur Poop!', 'aired': '2009-09-29'},
    {'seasonNumber': 1, 'number': 31, 'name': 'Night Train', 'aired': '2009-09-30'},
    {'seasonNumber': 1, 'number': 32, 'name': 'Fossil Fred', 'aired': '2009-09-30'},
    {'seasonNumber': 1, 'number': 63, 'name': 'Buck-Tooth Bucky', 'aired': '2010-09-27'},
    {'seasonNumber': 1, 'number': 64, 'name': "Tiny's Tiny Friend", 'aired': '2010-09-27'},
    {'seasonNumber': 1, 'number': 65, 'name': 'An Armored Tail Tale', 'aired': '2010-11-08'},
    {'seasonNumber': 1, 'number': 66, 'name': 'Pterosaur Flying Club', 'aired': '2010-11-08'},
    {'seasonNumber': 1, 'number': 74, 'name': 'Train Trouble', 'aired': '2010-10-11'},
    {'seasonNumber': 1, 'number': 75, 'name': 'The Amazing Michelinoceras Brothers', 'aired': '2010-11-19'},
    {'seasonNumber': 1, 'number': 76, 'name': "Dads' Day Out", 'aired': '2010-11-19'},
    {'seasonNumber': 1, 'number': 1, 'name': 'Valley of the Stygimolochs', 'aired': '2009-09-07'},
    {'seasonNumber': 1, 'number': 2, 'name': 'Tiny Loves Fish', 'aired': '2009-09-07'},
    {'seasonNumber': 1, 'number': 67, 'name': 'Great Big Stomping Dinosaur Feet!', 'aired': '2010-12-06'},
    {'seasonNumber': 1, 'number': 68, 'name': 'Hornucopia!', 'aired': '2010-12-06'},
    {'seasonNumber': 1, 'number': 29, 'name': 'The Old Spinosaurs And The Sea', 'aired': '2009-11-12'},
    {'seasonNumber': 1, 'number': 30, 'name': 'A Spiky Tail Tale', 'aired': '2009-11-12'},
    {'seasonNumber': 0, 'number': 1, 'name': 'Special Episode', 'aired': '2021-04-01'},
]

PAW_PATROL_EPISODES = [
    {'seasonNumber': 1, 'number': 1, 'name': 'Pups Make a Splash', 'aired': '2013-08-27'},
    {'seasonNumber': 1, 'number': 2, 'name': 'Pups Fall Festival', 'aired': '2013-08-27'},
    {'seasonNumber': 1, 'number': 3, 'name': 'Pups Save the Sea Turtles', 'aired': '2013-08-27'},
    {'seasonNumber': 1, 'number': 4, 'name': 'Pups and the Very Big Baby', 'aired': '2013-08-27'},
    {'seasonNumber': 1, 'number': 5, 'name': 'Pups and the Kitty-tastrophe', 'aired': '2013-08-30'},
    {'seasonNumber': 1, 'number': 6, 'name': 'Pups Save a Train', 'aired': '2013-08-30'},
    {'seasonNumber': 1, 'number': 7, 'name': 'Pup Pup Boogie', 'aired': '2013-09-05'},
    {'seasonNumber': 1, 'number': 8, 'name': 'Pups in a Fog', 'aired': '2013-09-05'},
    {'seasonNumber': 1, 'number': 9, 'name': 'Pup Pup Goose', 'aired': '2013-09-06'},
    {'seasonNumber': 1, 'number': 10, 'name': 'Pup Pup and Away', 'aired': '2013-09-06'},
    {'seasonNumber': 1, 'number': 11, 'name': 'Pups on Ice', 'aired': '2013-09-09'},
    {'seasonNumber': 1, 'number': 12, 'name': 'Pups and the Snow Monster', 'aired': '2013-09-09'},
    {'seasonNumber': 1, 'number': 13, 'name': 'Pups Save the Circus', 'aired': '2013-09-10'},
    {'seasonNumber': 1, 'number': 14, 'name': 'Pup A Doodle Do', 'aired': '2013-09-10'},
    {'seasonNumber': 1, 'number': 19, 'name': 'Pups and the Ghost Pirate', 'aired': '2013-09-13'},
    {'seasonNumber': 1, 'number': 20, 'name': 'Pups Get a Rubble', 'aired': '2013-09-17'},
    {'seasonNumber': 1, 'number': 21, 'name': 'Pups Save a Walrus', 'aired': '2013-09-17'},
    {'seasonNumber': 1, 'number': 22, 'name': 'Pups Save the Bunnies', 'aired': '2013-09-18'},
    {'seasonNumber': 1, 'number': 23, 'name': 'Puptacular', 'aired': '2013-09-18'},
    {'seasonNumber': 1, 'number': 32, 'name': 'Pups Save Christmas', 'aired': '2013-12-24'},
    {'seasonNumber': 1, 'number': 33, 'name': 'Pups Go All Monkey', 'aired': '2014-02-08'},
    {'seasonNumber': 1, 'number': 34, 'name': 'Pups Save a Hoot', 'aired': '2014-02-08'},
    {'seasonNumber': 1, 'number': 43, 'name': 'Pups Great Race', 'aired': '2014-03-15'},
    {'seasonNumber': 1, 'number': 44, 'name': 'Pups Take the Cake', 'aired': '2014-03-15'},
    {'seasonNumber': 1, 'number': 46, 'name': 'Pups Save the Easter Egg Hunt', 'aired': '2014-04-19'},
    {'seasonNumber': 1, 'number': 47, 'name': 'Pups Save a Super Pup', 'aired': '2014-08-02'},
    {'seasonNumber': 1, 'number': 48, 'name': "Pups Save Ryder's Robot", 'aired': '2014-08-02'},
]


# ═══════════════════════════════════════════════════════════════════════════
# 1. _clean_title tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCleanTitle(unittest.TestCase):

    def test_basic_dots_to_spaces(self):
        self.assertEqual(tv._clean_title('Pups.Make.a.Splash'), 'Pups Make a Splash')

    def test_removes_file_extension(self):
        self.assertEqual(tv._clean_title('Fast Friends.avi'), 'Fast Friends')

    def test_removes_mkv_extension(self):
        self.assertEqual(tv._clean_title('Title.mkv'), 'Title')

    def test_removes_1080p_and_after(self):
        result = tv._clean_title('Pups.Make.a.Splash.1080p.NF.WEBRip.DDP5.1.x264-LAZY')
        self.assertEqual(result, 'Pups Make a Splash')

    def test_removes_720p_and_after(self):
        result = tv._clean_title('Title.720p.BluRay.x264-GROUP')
        self.assertEqual(result, 'Title')

    def test_removes_square_bracket_tags(self):
        result = tv._clean_title('One Smart Dinosaur [SD 1Mbps MPEG4]')
        self.assertEqual(result, 'One Smart Dinosaur')

    def test_removes_hd_bracket_tag(self):
        result = tv._clean_title('Title [HD 10Mbps HEVC]')
        self.assertEqual(result, 'Title')

    def test_removes_parenthesized_year(self):
        result = tv._clean_title('Dinosaur Train (2009)')
        self.assertEqual(result, 'Dinosaur Train')

    def test_preserves_hyphenated_words(self):
        result = tv._clean_title('Pups.and.the.Kitty-tastrophe')
        self.assertEqual(result, 'Pups and the Kitty-tastrophe')

    def test_preserves_exclamation_marks(self):
        result = tv._clean_title('Now With Feathers!')
        self.assertEqual(result, 'Now With Feathers!')

    def test_preserves_apostrophes(self):
        result = tv._clean_title("I'm a T. Rex")
        # dot in T. becomes space, then multiple spaces collapse
        self.assertEqual(result, "I'm a T Rex")

    def test_preserves_multi_segment_title(self):
        result = tv._clean_title('Fast Friends - T Rex Teeth')
        self.assertEqual(result, 'Fast Friends - T Rex Teeth')

    def test_underscores_to_spaces(self):
        result = tv._clean_title('Fast_Friends_T_Rex')
        self.assertEqual(result, 'Fast Friends T Rex')

    def test_collapses_multiple_spaces(self):
        result = tv._clean_title('Title   With   Spaces')
        self.assertEqual(result, 'Title With Spaces')

    def test_strips_leading_trailing_junk(self):
        result = tv._clean_title(' - Title - ')
        self.assertEqual(result, 'Title')

    def test_paw_patrol_folder_name(self):
        result = tv._clean_title('Paw.Patrol.S01.1080p.NF.WEBRip.DDP5.1.x264-LAZY')
        # S01 isn't caught by _clean_title (stripped by guess_show_name separately)
        # but 1080p IS caught, so result should be "Paw Patrol S01" at most
        # Actually 1080p comes after S01, so the junk regex removes from .1080p onward
        self.assertEqual(result, 'Paw Patrol S01')

    def test_empty_string(self):
        self.assertEqual(tv._clean_title(''), '')

    def test_only_junk(self):
        result = tv._clean_title('1080p.NF.WEBRip')
        self.assertEqual(result, '')

    def test_ddp51_audio(self):
        result = tv._clean_title('Title.DDP5.1.x264')
        self.assertEqual(result, 'Title')

    def test_webrip_removal(self):
        result = tv._clean_title('Title.WEBRip.DDP5.1')
        self.assertEqual(result, 'Title')

    def test_removes_hevc(self):
        result = tv._clean_title('Title.HEVC')
        self.assertEqual(result, 'Title')


# ═══════════════════════════════════════════════════════════════════════════
# 2. _normalize tests
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalize(unittest.TestCase):

    def test_lowercase(self):
        self.assertEqual(tv._normalize('Hello World'), 'hello world')

    def test_strips_punctuation(self):
        self.assertEqual(tv._normalize("I'm a T. Rex!"), 'im a t rex')

    def test_collapses_whitespace(self):
        self.assertEqual(tv._normalize('Hello   World'), 'hello world')

    def test_hyphen_removed(self):
        self.assertEqual(tv._normalize('Buck-Tooth'), 'bucktooth')

    def test_puptacular_variants(self):
        self.assertEqual(tv._normalize('Pup-tacular'), tv._normalize('Puptacular'))


# ═══════════════════════════════════════════════════════════════════════════
# 3. _similarity tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSimilarity(unittest.TestCase):

    def test_identical(self):
        self.assertAlmostEqual(tv._similarity('Hello', 'Hello'), 1.0)

    def test_case_insensitive(self):
        self.assertAlmostEqual(tv._similarity('hello', 'HELLO'), 1.0)

    def test_punctuation_ignored(self):
        self.assertGreater(tv._similarity("I'm a T. Rex!", "Im a T Rex"), 0.9)

    def test_completely_different(self):
        self.assertLess(tv._similarity('AAAA', 'ZZZZ'), 0.2)

    def test_partial_match(self):
        sim = tv._similarity('Pups Make a Splash', 'Pups Make a Splash - Pups Fall Festival')
        self.assertGreater(sim, 0.5)

    def test_puptacular_hyphen(self):
        # "Pup-tacular" vs "Puptacular" should be very similar
        self.assertGreater(tv._similarity('Pup-tacular', 'Puptacular'), 0.9)


# ═══════════════════════════════════════════════════════════════════════════
# 4. parse_filename tests
# ═══════════════════════════════════════════════════════════════════════════

class TestParseFilename(unittest.TestCase):

    def test_paw_patrol_multi_episode(self):
        fp = r'F:\Shows\Paw.Patrol.S01E01E02.Pups.Make.a.Splash.-.Pups.Fall.Festival.1080p.NF.WEBRip.DDP5.1.x264-LAZY.mkv'
        r = tv.parse_filename(fp)
        self.assertTrue(r['has_sxxexx'])
        self.assertEqual(r['season'], 1)
        self.assertEqual(r['episodes'], [1, 2])
        self.assertEqual(r['show_name'], 'Paw Patrol')
        self.assertIn('Pups Make a Splash', r['episode_title'])
        self.assertIn('Pups Fall Festival', r['episode_title'])

    def test_paw_patrol_single_episode(self):
        fp = r'F:\Shows\Paw.Patrol.S01E19.Pups.and.the.Ghost.Pirate.1080p.NF.WEBRip.DDP5.1.x264-LAZY.mkv'
        r = tv.parse_filename(fp)
        self.assertTrue(r['has_sxxexx'])
        self.assertEqual(r['season'], 1)
        self.assertEqual(r['episodes'], [19])
        self.assertEqual(r['show_name'], 'Paw Patrol')
        self.assertEqual(r['episode_title'], 'Pups and the Ghost Pirate')

    def test_dinosaur_train_no_sxxexx(self):
        fp = r'F:\Downloads\Dinosaur Train\Fast Friends - T Rex Teeth.avi'
        r = tv.parse_filename(fp)
        self.assertFalse(r['has_sxxexx'])
        self.assertIsNone(r['show_name'])  # show_name comes from folder, not filename
        self.assertEqual(r['episode_title'], 'Fast Friends - T Rex Teeth')

    def test_dinosaur_train_with_sxxexx(self):
        fp = r'F:\Downloads\Dinosaur Train\s01e07 Now With Feathers! - A Frill a Minute.avi'
        r = tv.parse_filename(fp)
        self.assertTrue(r['has_sxxexx'])
        self.assertEqual(r['season'], 1)
        self.assertEqual(r['episodes'], [7])
        # show_name should be None (nothing before "s01e07")
        self.assertFalse(r['show_name'])  # empty string or None
        self.assertIn('Now With Feathers!', r['episode_title'])
        self.assertIn('A Frill a Minute', r['episode_title'])

    def test_dinosaur_train_with_metadata_tags(self):
        fp = r'F:\Downloads\Dinosaur Train\One Smart Dinosaur - Petey the Peteinosaurus [SD 1Mbps MPEG4].mkv'
        r = tv.parse_filename(fp)
        self.assertFalse(r['has_sxxexx'])
        self.assertIsNone(r['show_name'])
        # Bracket tags should be stripped
        self.assertNotIn('[', r['episode_title'])
        self.assertIn('One Smart Dinosaur', r['episode_title'])

    def test_single_title_no_sxxexx(self):
        fp = r'F:\Downloads\Dinosaur Train\Train Trouble.avi'
        r = tv.parse_filename(fp)
        self.assertFalse(r['has_sxxexx'])
        self.assertIsNone(r['show_name'])
        self.assertEqual(r['episode_title'], 'Train Trouble')

    def test_buck_tooth_bucky_missing_space(self):
        fp = "F:\\Downloads\\Dinosaur Train\\Buck-Tooth Bucky- Tiny's Tiny Friend.avi"
        r = tv.parse_filename(fp)
        self.assertFalse(r['has_sxxexx'])
        self.assertIn('Buck-Tooth Bucky', r['episode_title'])
        self.assertIn("Tiny's Tiny Friend", r['episode_title'])

    def test_three_digit_episode_number(self):
        fp = r'F:\Shows\Show.S01E100.Title.mkv'
        r = tv.parse_filename(fp)
        self.assertTrue(r['has_sxxexx'])
        self.assertEqual(r['episodes'], [100])

    def test_no_show_name_when_sxxexx_at_start(self):
        fp = r'F:\Shows\S01E05 Episode Title.mkv'
        r = tv.parse_filename(fp)
        self.assertTrue(r['has_sxxexx'])
        self.assertFalse(r['show_name'])
        self.assertEqual(r['episode_title'], 'Episode Title')

    def test_episode_title_extraction_with_dots(self):
        fp = r'F:\Shows\Show.Name.S01E05.Episode.Title.mkv'
        r = tv.parse_filename(fp)
        self.assertEqual(r['show_name'], 'Show Name')
        self.assertEqual(r['episode_title'], 'Episode Title')


# ═══════════════════════════════════════════════════════════════════════════
# 5. guess_show_name tests
# ═══════════════════════════════════════════════════════════════════════════

class TestGuessShowName(unittest.TestCase):

    def test_simple_folder(self):
        fp = r'F:\Downloads\Dinosaur Train\episode.avi'
        self.assertEqual(tv.guess_show_name(fp), 'Dinosaur Train')

    def test_release_group_folder(self):
        fp = r'F:\Downloads\Paw.Patrol.S01.1080p.NF.WEBRip.DDP5.1.x264-LAZY\episode.mkv'
        result = tv.guess_show_name(fp)
        self.assertEqual(result, 'Paw Patrol')

    def test_season_folder_skipped(self):
        fp = r'F:\Shows\Dinosaur Train\Season 1\episode.avi'
        self.assertEqual(tv.guess_show_name(fp), 'Dinosaur Train')

    def test_season_folder_lowercase(self):
        fp = r'F:\Shows\My Show\season 2\episode.avi'
        self.assertEqual(tv.guess_show_name(fp), 'My Show')

    def test_s_folder_skipped(self):
        fp = r'F:\Shows\Dinosaur Train\S01\episode.avi'
        self.assertEqual(tv.guess_show_name(fp), 'Dinosaur Train')

    def test_strips_trailing_season_indicator(self):
        fp = r'F:\Downloads\Paw.Patrol.S01.1080p.NF.WEBRip.DDP5.1.x264-LAZY\ep.mkv'
        result = tv.guess_show_name(fp)
        # Should be "Paw Patrol" not "Paw Patrol S01"
        self.assertEqual(result, 'Paw Patrol')

    def test_folder_with_year(self):
        fp = r'F:\Shows\Dinosaur.Train.2009.S01\episode.mkv'
        result = tv.guess_show_name(fp)
        # _clean_title removes (2009) but not bare 2009 after dots...
        # After dots→spaces: "Dinosaur Train 2009 S01"
        # S01 stripped → "Dinosaur Train 2009"
        self.assertIn('Dinosaur Train', result)


# ═══════════════════════════════════════════════════════════════════════════
# 6. match_episode_by_title tests (single part)
# ═══════════════════════════════════════════════════════════════════════════

class TestMatchSinglePart(unittest.TestCase):

    def test_exact_match(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Fast Friends', season_hint=1)
        self.assertTrue(results)
        self.assertEqual(results[0][0]['number'], 11)
        self.assertGreater(results[0][1], 0.9)

    def test_match_with_punctuation(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, "I'm a T. Rex!", season_hint=1)
        self.assertTrue(results)
        self.assertEqual(results[0][0]['number'], 7)

    def test_match_exclamation(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Dinosaur Poop!', season_hint=1)
        self.assertTrue(results)
        self.assertEqual(results[0][0]['number'], 20)

    def test_match_train_trouble_single(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Train Trouble', season_hint=1)
        self.assertTrue(results)
        self.assertEqual(results[0][0]['number'], 74)
        self.assertGreater(results[0][1], 0.9)

    def test_paw_patrol_exact(self):
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Make a Splash', season_hint=1)
        self.assertTrue(results)
        self.assertEqual(results[0][0]['number'], 1)

    def test_paw_patrol_ghost_pirate(self):
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups and the Ghost Pirate', season_hint=1)
        self.assertTrue(results)
        self.assertEqual(results[0][0]['number'], 19)

    def test_no_match_for_garbage(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'xyzzy nonsense garbage', season_hint=1)
        # Any matches should be very low quality
        for ep, score in results:
            self.assertLess(score, 0.5)

    def test_specials_excluded_with_hint(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Special Episode', season_hint=1)
        # Should not match the S00E01 special when looking for season 1
        for ep, _ in results:
            self.assertNotEqual(ep['seasonNumber'], 0)

    def test_specials_included_without_hint(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Special Episode', season_hint=None)
        self.assertTrue(results)

    def test_puptacular_matches_hyphenated(self):
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pup-tacular', season_hint=1)
        self.assertTrue(results)
        self.assertEqual(results[0][0]['name'], 'Puptacular')

    def test_empty_title(self):
        results = tv.match_episode_by_title(DINOSAUR_TRAIN_EPISODES, '', season_hint=1)
        self.assertEqual(results, [])

    def test_empty_episodes(self):
        results = tv.match_episode_by_title([], 'Fast Friends', season_hint=1)
        self.assertEqual(results, [])


# ═══════════════════════════════════════════════════════════════════════════
# 7. match_episode_by_title tests (multi-part)
# ═══════════════════════════════════════════════════════════════════════════

class TestMatchMultiPart(unittest.TestCase):

    def test_dinosaur_train_dual_segment(self):
        """'Fast Friends - T Rex Teeth' should match S01E11 + S01E12."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Fast Friends - T Rex Teeth', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [11, 12])

    def test_dinosaur_train_night_train_fossil_fred(self):
        """'Night Train - Fossil Fred' should match S01E31 + S01E32."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Night Train - Fossil Fred', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [31, 32])

    def test_dinosaur_train_armored_pterosaur(self):
        """'An Armored Tail Tale - Pterosaur Flying Club' → S01E65 + S01E66."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'An Armored Tail Tale - Pterosaur Flying Club', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [65, 66])

    def test_dinosaur_train_im_a_trex_ned(self):
        """'I'm a T. Rex - Ned the Quadruped' → S01E07 + S01E08."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, "I'm a T. Rex - Ned the Quadruped", season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [7, 8])

    def test_dinosaur_train_laura_dinosaur_poop(self):
        """'Laura The Giganotosaurus - Dinosaur Poop!' → S01E19 + S01E20."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Laura The Giganotosaurus - Dinosaur Poop!', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [19, 20])

    def test_dinosaur_train_bucktooth_bucky_no_space_before_dash(self):
        """'Buck-Tooth Bucky- Tiny's Tiny Friend' (no space before dash)."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, "Buck-Tooth Bucky- Tiny's Tiny Friend", season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [63, 64])

    def test_paw_patrol_dual_segment(self):
        """'Pups Make a Splash - Pups Fall Festival' → S01E01 + S01E02."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Make a Splash - Pups Fall Festival', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [1, 2])

    def test_paw_patrol_boogie_fog(self):
        """'Pup Pup Boogie - Pups in a Fog' → S01E07 + S01E08."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pup Pup Boogie - Pups in a Fog', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [7, 8])

    def test_paw_patrol_kitty_tastrophe_train(self):
        """'Pups and the Kitty-tastrophe - Pups Save a Train' → S01E05 + S01E06."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups and the Kitty-tastrophe - Pups Save a Train', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [5, 6])

    def test_paw_patrol_circus_doodle(self):
        """'Pups Save the Circus - Pup a Doodle Do' → S01E13 + S01E14."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Save the Circus - Pup a Doodle Do', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [13, 14])

    def test_paw_patrol_bunnies_puptacular(self):
        """'Pups Save the Bunnies - Pup-tacular' → S01E22 + S01E23."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Save the Bunnies - Pup-tacular', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [22, 23])

    def test_paw_patrol_great_race_cake(self):
        """'Pups Great Race - Pups Take the Cake' → S01E43 + S01E44."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Great Race - Pups Take the Cake', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [43, 44])

    def test_dinosaur_train_amazing_michelinoceras_dads(self):
        """'The Amazing Michelinoceras Brothers - Dads' Day Out' → S01E75 + S01E76."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, "The Amazing Michelinoceras Brothers - Dads' Day Out", season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [75, 76])

    def test_dinosaur_train_stomping_feet_hornucopia(self):
        """'Great Big Stomping Dinosaur Feet! - Hornucopia!' → S01E67 + S01E68."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Great Big Stomping Dinosaur Feet! - Hornucopia!', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [67, 68])

    def test_dinosaur_train_spinosaurs_spiky(self):
        """'The Old Spinosaurs And The Sea - A Spiky Tail Tale' → S01E29 + S01E30."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'The Old Spinosaurs And The Sea - A Spiky Tail Tale', season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [29, 30])

    def test_results_sorted_by_episode_number(self):
        """Multi-part results should be sorted by episode number."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Fall Festival - Pups Make a Splash', season_hint=1)
        # Even though part order is reversed, results should be sorted by ep number
        self.assertEqual(len(results), 2)
        self.assertLessEqual(results[0][0]['number'], results[1][0]['number'])

    def test_no_duplicate_episode_matching(self):
        """Each episode should only match one part."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Make a Splash - Pups Make a Splash', season_hint=1)
        # Should get 2 results but with different episodes
        nums = [r[0]['number'] for r in results]
        self.assertEqual(len(set(nums)), len(nums))  # all unique


# ═══════════════════════════════════════════════════════════════════════════
# 8. match_episodes_for_ordering tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMatchEpisodesForOrdering(unittest.TestCase):

    def test_single_episode(self):
        result = tv.match_episodes_for_ordering(PAW_PATROL_EPISODES, 1, [19])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'Pups and the Ghost Pirate')

    def test_multi_episode(self):
        result = tv.match_episodes_for_ordering(PAW_PATROL_EPISODES, 1, [1, 2])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], 'Pups Make a Splash')
        self.assertEqual(result[1]['name'], 'Pups Fall Festival')

    def test_no_match(self):
        result = tv.match_episodes_for_ordering(PAW_PATROL_EPISODES, 1, [999])
        self.assertEqual(len(result), 0)

    def test_wrong_season(self):
        result = tv.match_episodes_for_ordering(PAW_PATROL_EPISODES, 2, [1])
        self.assertEqual(len(result), 0)

    def test_sorted_by_number(self):
        result = tv.match_episodes_for_ordering(PAW_PATROL_EPISODES, 1, [8, 7])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['number'], 7)
        self.assertEqual(result[1]['number'], 8)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Integration: parse → match pipeline (no API)
# ═══════════════════════════════════════════════════════════════════════════

class TestParseAndMatch(unittest.TestCase):
    """Test the full parse_filename → match_episode_by_title pipeline."""

    def _match(self, filepath, episodes, season_hint=1):
        parsed = tv.parse_filename(filepath)
        title = parsed.get('episode_title')
        if not title:
            return []
        return tv.match_episode_by_title(episodes, title, season_hint=season_hint)

    def test_dinosaur_train_bare_dual(self):
        fp = r'F:\Downloads\Dinosaur Train\Fast Friends - T Rex Teeth.avi'
        results = self._match(fp, DINOSAUR_TRAIN_EPISODES)
        self.assertEqual(len(results), 2)
        self.assertEqual([r[0]['number'] for r in results], [11, 12])

    def test_dinosaur_train_sxxexx_dual(self):
        fp = r'F:\Downloads\Dinosaur Train\s01e07 Now With Feathers! - A Frill a Minute.avi'
        results = self._match(fp, DINOSAUR_TRAIN_EPISODES)
        self.assertEqual(len(results), 2)
        self.assertEqual([r[0]['number'] for r in results], [13, 14])

    def test_dinosaur_train_single(self):
        fp = r'F:\Downloads\Dinosaur Train\Train Trouble.avi'
        results = self._match(fp, DINOSAUR_TRAIN_EPISODES)
        self.assertTrue(len(results) >= 1)
        # Best match should be Train Trouble
        self.assertEqual(results[0][0]['number'], 74)

    def test_paw_patrol_full_filename(self):
        fp = r'F:\Downloads\Paw.Patrol.S01\Paw.Patrol.S01E07E08.Pup.Pup.Boogie.-.Pups.in.a.Fog.1080p.NF.WEBRip.DDP5.1.x264-LAZY.mkv'
        results = self._match(fp, PAW_PATROL_EPISODES)
        self.assertEqual(len(results), 2)
        self.assertEqual([r[0]['number'] for r in results], [7, 8])

    def test_paw_patrol_single_ep(self):
        fp = r'F:\Shows\Paw.Patrol.S01E19.Pups.and.the.Ghost.Pirate.1080p.NF.WEBRip.DDP5.1.x264-LAZY.mkv'
        results = self._match(fp, PAW_PATROL_EPISODES)
        self.assertTrue(len(results) >= 1)
        # Best match should be Pups and the Ghost Pirate
        self.assertEqual(results[0][0]['number'], 19)

    def test_paw_patrol_christmas(self):
        fp = r'F:\Shows\Paw.Patrol.S01E20.Pups.Save.Christmas.1080p.NF.WEBRip.DDP5.1.x264-LAZY.mkv'
        results = self._match(fp, PAW_PATROL_EPISODES)
        self.assertTrue(len(results) >= 1)
        # Best match should be Pups Save Christmas (TVDB S01E32)
        self.assertEqual(results[0][0]['number'], 32)

    def test_dinosaur_train_metadata_stripped(self):
        fp = r'F:\Downloads\Dinosaur Train\One Smart Dinosaur - Petey the Peteinosaurus [SD 1Mbps MPEG4].mkv'
        results = self._match(fp, DINOSAUR_TRAIN_EPISODES)
        self.assertEqual(len(results), 2)
        self.assertEqual([r[0]['number'] for r in results], [9, 10])


# ═══════════════════════════════════════════════════════════════════════════
# 10. SxxExx tag generation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSxxExxTagGeneration(unittest.TestCase):
    """Test that matched episodes produce correct SxxExx tags."""

    def _get_tag(self, results):
        if not results:
            return ''
        ep_list = [(r[0]['seasonNumber'], r[0]['number']) for r in results]
        season = ep_list[0][0]
        return f'S{season:02d}' + ''.join(f'E{e:02d}' for _, e in ep_list)

    def test_single_episode_tag(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Train Trouble', season_hint=1)
        # For single-part, take only the best match for tag
        self.assertEqual(self._get_tag(results[:1]), 'S01E74')

    def test_dual_episode_tag(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Fast Friends - T Rex Teeth', season_hint=1)
        self.assertEqual(self._get_tag(results), 'S01E11E12')

    def test_paw_patrol_dual_tag(self):
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Make a Splash - Pups Fall Festival', season_hint=1)
        self.assertEqual(self._get_tag(results), 'S01E01E02')

    def test_high_number_dual_tag(self):
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'An Armored Tail Tale - Pterosaur Flying Club', season_hint=1)
        self.assertEqual(self._get_tag(results), 'S01E65E66')

    def test_paw_patrol_christmas_single_tag(self):
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Save Christmas', season_hint=1)
        # For single-part, take only the best match for tag
        self.assertEqual(self._get_tag(results[:1]), 'S01E32')


# ═══════════════════════════════════════════════════════════════════════════
# 11. Cache tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCache(unittest.TestCase):

    def setUp(self):
        tv._cache = {}

    def test_cache_set_and_get(self):
        tv._cache_set('test_key', {'hello': 'world'})
        result = tv._cache_get('test_key')
        self.assertEqual(result, {'hello': 'world'})

    def test_cache_miss(self):
        result = tv._cache_get('nonexistent')
        self.assertIsNone(result)

    def test_cache_expiry(self):
        tv._cache = {
            'old_key': {'data': 'old', '_ts': 0}  # epoch time = expired
        }
        result = tv._cache_get('old_key')
        self.assertIsNone(result)

    def test_cache_fresh(self):
        import time
        tv._cache = {
            'fresh_key': {'data': 'fresh', '_ts': time.time()}
        }
        result = tv._cache_get('fresh_key')
        self.assertEqual(result, 'fresh')


# ═══════════════════════════════════════════════════════════════════════════
# 12. Edge case tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):

    def test_parse_empty_filename(self):
        r = tv.parse_filename('')
        self.assertFalse(r['has_sxxexx'])

    def test_parse_no_extension(self):
        r = tv.parse_filename(r'F:\Shows\S01E01 Title')
        self.assertTrue(r['has_sxxexx'])
        self.assertEqual(r['episodes'], [1])

    def test_clean_title_only_brackets(self):
        result = tv._clean_title('[HD 10Mbps HEVC]')
        self.assertEqual(result, '')

    def test_normalize_empty(self):
        self.assertEqual(tv._normalize(''), '')

    def test_similarity_empty_strings(self):
        self.assertEqual(tv._similarity('', ''), 1.0)

    def test_match_with_none_episode_name(self):
        eps = [{'seasonNumber': 1, 'number': 1, 'name': None}]
        results = tv.match_episode_by_title(eps, 'Test', season_hint=1)
        self.assertEqual(results, [])

    def test_match_with_empty_episode_name(self):
        eps = [{'seasonNumber': 1, 'number': 1, 'name': ''}]
        results = tv.match_episode_by_title(eps, 'Test', season_hint=1)
        self.assertEqual(results, [])

    def test_guess_show_name_root_path(self):
        # Edge case: file at drive root
        result = tv.guess_show_name(r'F:\episode.avi')
        # Should return something (the empty string cleaned, or None)
        # Not crash

    def test_parse_sxxexx_case_insensitive(self):
        r = tv.parse_filename(r'F:\Shows\show.S01E01.title.mkv')
        self.assertTrue(r['has_sxxexx'])
        r2 = tv.parse_filename(r'F:\Shows\show.s01e01.title.mkv')
        self.assertTrue(r2['has_sxxexx'])

    def test_multi_part_with_en_dash(self):
        """En dash (–) should also work as separator."""
        results = tv.match_episode_by_title(
            DINOSAUR_TRAIN_EPISODES, 'Fast Friends – T Rex Teeth', season_hint=1)
        self.assertEqual(len(results), 2)

    def test_paw_patrol_super_pup_ryders_robot(self):
        """'Pups Save a Super Pup - Pups Save Ryders Robot' matching."""
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, "Pups Save a Super Pup - Pups Save Ryders Robot", season_hint=1)
        self.assertEqual(len(results), 2)
        nums = [r[0]['number'] for r in results]
        self.assertEqual(nums, [47, 48])

    def test_paw_patrol_monkey_hoot(self):
        """'Pups Save a Monkey - Pups Save a Hoot' → Pups Go All Monkey + Pups Save a Hoot."""
        # Note: file says "Pups Save a Monkey" but TVDB says "Pups Go All Monkey"
        results = tv.match_episode_by_title(
            PAW_PATROL_EPISODES, 'Pups Save a Monkey - Pups Save a Hoot', season_hint=1)
        # "Pups Save a Monkey" may not exactly match "Pups Go All Monkey" well
        # but "Pups Save a Hoot" should match perfectly
        # At minimum we should get at least the hoot match
        self.assertTrue(len(results) >= 1)
        hoot_matched = any(r[0]['number'] == 34 for r in results)
        self.assertTrue(hoot_matched)


# ═══════════════════════════════════════════════════════════════════════════
# 13. High-level feature mocked tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLookupYearMocked(unittest.TestCase):

    @patch('tvdb_lookup.find_best_series')
    def test_year_found(self, mock_find):
        mock_find.return_value = [('12345', 'Dinosaur Train', '2009', 0.95)]
        year, conf, name = tv.lookup_year(
            r'F:\Downloads\Dinosaur Train\episode.avi')
        self.assertEqual(year, '2009')
        self.assertGreater(conf, 0.5)
        self.assertEqual(name, 'Dinosaur Train')

    @patch('tvdb_lookup.find_best_series')
    def test_year_low_confidence(self, mock_find):
        mock_find.return_value = [('12345', 'Wrong Show', '2020', 0.3)]
        year, conf, name = tv.lookup_year(
            r'F:\Downloads\Dinosaur Train\episode.avi')
        self.assertIsNone(year)

    @patch('tvdb_lookup.find_best_series')
    def test_year_no_results(self, mock_find):
        mock_find.return_value = []
        year, conf, name = tv.lookup_year(
            r'F:\Downloads\Dinosaur Train\episode.avi')
        self.assertIsNone(year)


class TestLookupEpisodeIdMocked(unittest.TestCase):

    @patch('tvdb_lookup.get_series_episodes')
    @patch('tvdb_lookup.get_season_types')
    @patch('tvdb_lookup.find_best_series')
    def test_dual_segment_dinosaur_train(self, mock_find, mock_types, mock_eps):
        mock_find.return_value = [('116291', 'Dinosaur Train', '2009', 0.95)]
        mock_types.return_value = [{'type': 'default', 'name': 'Aired Order'}]
        mock_eps.return_value = DINOSAUR_TRAIN_EPISODES

        result = tv.lookup_episode_id(
            r'F:\Downloads\Dinosaur Train\Fast Friends - T Rex Teeth.avi')
        self.assertIsNotNone(result)
        aired = result['orderings'].get('Aired Order')
        self.assertIsNotNone(aired)
        self.assertEqual(aired['tag'], 'S01E11E12')
        self.assertGreater(aired['match_score'], 0.5)

    @patch('tvdb_lookup.get_series_episodes')
    @patch('tvdb_lookup.get_season_types')
    @patch('tvdb_lookup.find_best_series')
    def test_single_episode_dinosaur_train(self, mock_find, mock_types, mock_eps):
        mock_find.return_value = [('116291', 'Dinosaur Train', '2009', 0.95)]
        mock_types.return_value = [{'type': 'default', 'name': 'Aired Order'}]
        mock_eps.return_value = DINOSAUR_TRAIN_EPISODES

        result = tv.lookup_episode_id(
            r'F:\Downloads\Dinosaur Train\Train Trouble.avi')
        self.assertIsNotNone(result)
        aired = result['orderings'].get('Aired Order')
        self.assertIsNotNone(aired)
        self.assertEqual(aired['tag'], 'S01E74')

    @patch('tvdb_lookup.get_series_episodes')
    @patch('tvdb_lookup.get_season_types')
    @patch('tvdb_lookup.find_best_series')
    def test_paw_patrol_with_sxxexx(self, mock_find, mock_types, mock_eps):
        mock_find.return_value = [('272472', 'PAW Patrol', '2013', 0.90)]
        mock_types.return_value = [{'type': 'default', 'name': 'Aired Order'}]
        mock_eps.return_value = PAW_PATROL_EPISODES

        fp = r'F:\Downloads\Paw.Patrol.S01.1080p.NF.WEBRip.DDP5.1.x264-LAZY\Paw.Patrol.S01E07E08.Pup.Pup.Boogie.-.Pups.in.a.Fog.1080p.NF.WEBRip.DDP5.1.x264-LAZY.mkv'
        result = tv.lookup_episode_id(fp)
        self.assertIsNotNone(result)
        aired = result['orderings'].get('Aired Order')
        self.assertIsNotNone(aired)
        self.assertEqual(aired['tag'], 'S01E07E08')

    @patch('tvdb_lookup.get_series_episodes')
    @patch('tvdb_lookup.get_season_types')
    @patch('tvdb_lookup.find_best_series')
    def test_paw_patrol_christmas_renumbered(self, mock_find, mock_types, mock_eps):
        """File says S01E20 but TVDB has Christmas as S01E32."""
        mock_find.return_value = [('272472', 'PAW Patrol', '2013', 0.90)]
        mock_types.return_value = [{'type': 'default', 'name': 'Aired Order'}]
        mock_eps.return_value = PAW_PATROL_EPISODES

        fp = r'F:\Downloads\Paw.Patrol.S01.1080p.NF.WEBRip.DDP5.1.x264-LAZY\Paw.Patrol.S01E20.Pups.Save.Christmas.1080p.NF.WEBRip.DDP5.1.x264-LAZY.mkv'
        result = tv.lookup_episode_id(fp)
        self.assertIsNotNone(result)
        aired = result['orderings'].get('Aired Order')
        self.assertIsNotNone(aired)
        self.assertEqual(aired['tag'], 'S01E32')

    @patch('tvdb_lookup.get_series_episodes')
    @patch('tvdb_lookup.get_season_types')
    @patch('tvdb_lookup.find_best_series')
    def test_query_title_passed_through(self, mock_find, mock_types, mock_eps):
        mock_find.return_value = [('116291', 'Dinosaur Train', '2009', 0.95)]
        mock_types.return_value = [{'type': 'default', 'name': 'Aired Order'}]
        mock_eps.return_value = DINOSAUR_TRAIN_EPISODES

        result = tv.lookup_episode_id(
            r'F:\Downloads\Dinosaur Train\Night Train - Fossil Fred.avi')
        self.assertIn('Night Train', result['query_title'])
        self.assertIn('Fossil Fred', result['query_title'])


class TestLookupEpisodeTitleMocked(unittest.TestCase):

    @patch('tvdb_lookup.get_series_episodes')
    @patch('tvdb_lookup.get_season_types')
    @patch('tvdb_lookup.find_best_series')
    def test_single_episode_title(self, mock_find, mock_types, mock_eps):
        mock_find.return_value = [('272472', 'PAW Patrol', '2013', 0.90)]
        mock_types.return_value = [{'type': 'default', 'name': 'Aired Order'}]
        mock_eps.return_value = PAW_PATROL_EPISODES

        fp = r'F:\Shows\Paw.Patrol.S01E19.title.mkv'
        result = tv.lookup_episode_title(fp)
        self.assertIsNotNone(result)
        aired = result['orderings'].get('Aired Order')
        self.assertIsNotNone(aired)
        self.assertEqual(len(aired), 1)
        self.assertEqual(aired[0][2], 'Pups and the Ghost Pirate')

    @patch('tvdb_lookup.get_series_episodes')
    @patch('tvdb_lookup.get_season_types')
    @patch('tvdb_lookup.find_best_series')
    def test_multi_episode_titles(self, mock_find, mock_types, mock_eps):
        mock_find.return_value = [('272472', 'PAW Patrol', '2013', 0.90)]
        mock_types.return_value = [{'type': 'default', 'name': 'Aired Order'}]
        mock_eps.return_value = PAW_PATROL_EPISODES

        fp = r'F:\Shows\Paw.Patrol.S01E01E02.title.mkv'
        result = tv.lookup_episode_title(fp)
        self.assertIsNotNone(result)
        aired = result['orderings'].get('Aired Order')
        self.assertEqual(len(aired), 2)
        self.assertEqual(aired[0][2], 'Pups Make a Splash')
        self.assertEqual(aired[1][2], 'Pups Fall Festival')

    @patch('tvdb_lookup.find_best_series')
    def test_no_sxxexx_returns_none(self, mock_find):
        fp = r'F:\Shows\episode without sxxexx.avi'
        result = tv.lookup_episode_title(fp)
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════
# 14. find_best_series tests (mocked search)
# ═══════════════════════════════════════════════════════════════════════════

class TestFindBestSeries(unittest.TestCase):

    @patch('tvdb_lookup.search_series')
    def test_exact_match_first(self, mock_search):
        mock_search.return_value = [
            {'name': 'Dinosaur Train', 'year': '2009', 'tvdb_id': '116291'},
            {'name': 'Dinosaur King', 'year': '2007', 'tvdb_id': '99999'},
        ]
        results = tv.find_best_series('Dinosaur Train')
        self.assertEqual(results[0][0], '116291')
        self.assertGreater(results[0][3], results[1][3])

    @patch('tvdb_lookup.search_series')
    def test_empty_results(self, mock_search):
        mock_search.return_value = []
        results = tv.find_best_series('Nonexistent Show')
        self.assertEqual(results, [])

    @patch('tvdb_lookup.search_series')
    def test_paw_patrol_match(self, mock_search):
        mock_search.return_value = [
            {'name': 'PAW Patrol', 'year': '2013', 'tvdb_id': '272472'},
        ]
        results = tv.find_best_series('Paw Patrol')
        self.assertTrue(results)
        self.assertGreater(results[0][3], 0.8)


if __name__ == '__main__':
    unittest.main()
