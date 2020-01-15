import datetime
import operator

from .db import execute_one, execute, COEFFICIENTS
from .models import Player


def get_highlights(skill_db, day: datetime.datetime) -> dict:
    round_range, rounds_played = get_round_range_for_day(skill_db, day)

    if rounds_played == 0:
        raise StopIteration

    lowest_rating, highest_rating = get_rating_extremes_between_rounds(
            skill_db, round_range)

    try:
        most_mvps = get_player_with_most_mvps_between_rounds(
                skill_db, round_range)
    except StopIteration:
        most_mvps = None

    skill_group_changes = get_skill_changes_between_rounds(
            skill_db, round_range)
    most_played_maps = get_most_played_maps_between_rounds(
            skill_db, round_range)
    skill_group_changes = [
        {
            'player_id': previous_skill.player_id,
            'steam_name': previous_skill.steam_name,
            'previous_skill': {
                'mmr': previous_skill.mmr,
                'skill_group': previous_skill.skill_group,
            },
            'next_skill': {
                'mmr': next_skill.mmr,
                'skill_group': next_skill.skill_group,
            },
        }
        for (previous_skill, next_skill) in skill_group_changes
    ]
    time_window = [day.isoformat(),
                   (day + datetime.timedelta(days=1)).isoformat()]
    return {
        'time_window': time_window,
        'rounds_played': rounds_played,
        'lowest_rating': lowest_rating,
        'highest_rating': highest_rating,
        'season_skill_group_changes': skill_group_changes,
        'most_played_maps': most_played_maps,
        'most_mvps': most_mvps,
    }


def get_most_played_maps_between_rounds(
        skill_db, round_range: (int, int)) -> {str: int}:
    return dict(execute(skill_db, '''
    SELECT map_name
         , COUNT(*) AS round_count
    FROM rounds
    JOIN maps ON rounds.map_id = maps.map_id
    WHERE round_id BETWEEN ? AND ?
    GROUP BY map_name
    ORDER BY round_count DESC
    ''', round_range))


def get_rating_extremes_between_rounds(skill_db, round_range: (int, int)) \
        -> (dict, dict):
    rating_extreme_rows = execute(skill_db, '''
    WITH components AS (
            SELECT rc.player_id
                 , AVG(rc.kill_rating) AS average_kills
                 , AVG(rc.death_rating) AS average_deaths
                 , AVG(rc.damage_rating) AS average_damage
                 , AVG(rc.kas_rating) AS average_kas
                 , COUNT(*) AS rounds_played
            FROM rating_components rc
            WHERE rc.round_id BETWEEN ? AND ?
            GROUP BY rc.player_id
        ), impact_ratings AS (
            SELECT c.player_id
                 , {} * c.average_kills
                 + {} * c.average_deaths
                 + {} * c.average_damage
                 + {} * c.average_kas
                 + {} AS rating
                 , c.*
            FROM components c
        )
    SELECT players.player_id
         , players.steam_name
         , ir.rating
         , ir.average_kills
         , -ir.average_deaths AS average_deaths
         , ir.average_damage
         , ir.average_kas
         , ir.rounds_played
    FROM players
    JOIN impact_ratings ir
    ON   players.player_id = ir.player_id
    AND  ir.rating IN (
        (SELECT MIN(rating) FROM impact_ratings),
        (SELECT MAX(rating) FROM impact_ratings)
    )
    '''.format(*COEFFICIENTS), round_range)

    rating_extremes = [
        {
            'player_id': rating_row[0],
            'steam_name': rating_row[1],
            'impact_rating': rating_row[2],
            'average_kills': rating_row[3],
            'average_deaths': rating_row[4],
            'average_damage': rating_row[5],
            'average_kas': rating_row[6],
            'rounds_played': rating_row[7],
        }
        for rating_row in rating_extreme_rows
    ]
    rating_extremes.sort(key=operator.itemgetter('impact_rating'))

    return rating_extremes[0], rating_extremes[-1]


def get_player_with_most_mvps_between_rounds(
        skill_db, round_range: (int, int)) -> dict:
    mvp = execute_one(skill_db, '''
    SELECT players.player_id
         , players.steam_name
         , COUNT(*) AS mvps
    FROM players
    JOIN rounds
    ON players.player_id = rounds.mvp
    WHERE rounds.round_id BETWEEN ? AND ?
    GROUP BY players.player_id
           , players.steam_name
    ORDER BY mvps DESC
    ''', round_range)
    return {
        'player_id': mvp[0],
        'steam_name': mvp[1],
        'mvps': mvp[2],
    }


def get_skill_changes_between_rounds(skill_db, round_range: (int, int)) \
        -> [(Player, Player)]:
    skill_change_rows = execute(skill_db, '''
    SELECT players.player_id
         , players.steam_name
         , earlier_ssh.skill_mean  AS earlier_skill_mean
         , earlier_ssh.skill_stdev AS earlier_skill_stdev
         , later_ssh.skill_mean    AS later_skill_mean
         , later_ssh.skill_stdev   AS later_skill_stdev
    FROM players
    JOIN season_skill_history earlier_ssh
    ON players.player_id = earlier_ssh.player_id
    AND earlier_ssh.round_id =
        ( SELECT MAX(ssh_before.round_id)
          FROM season_skill_history ssh_before
          WHERE ssh_before.round_id <= ?
          AND ssh_before.player_id = players.player_id
        )
    JOIN season_skill_history later_ssh
    ON players.player_id = later_ssh.player_id
    AND later_ssh.round_id =
        ( SELECT MIN(ssh_after.round_id)
          FROM season_skill_history ssh_after
          WHERE ssh_after.round_id >= ?
          AND ssh_after.player_id = players.player_id
        )
    ''', round_range)

    skill_changes = [
        (
            Player(player_id, steam_name, earlier_mean, earlier_stdev, 0.0),
            Player(player_id, steam_name, later_mean, later_stdev, 0.0),
        )
        for player_id, steam_name, earlier_mean, earlier_stdev,
            later_mean, later_stdev
        in skill_change_rows
    ]
    skill_changes.sort(key=lambda change: -change[1].mmr)

    return [
        (previous_skill, next_skill)
        for previous_skill, next_skill
        in skill_changes
        if previous_skill.skill_group != next_skill.skill_group
    ]


def get_round_range_for_day(skill_db, day: datetime.datetime) \
        -> ((int, int), int):
    next_day = day + datetime.timedelta(days=1)

    first_round, last_round, round_count = execute_one(skill_db, '''
    SELECT MIN(round_id)
         , MAX(round_id)
         , COUNT(*)
    FROM rounds
    WHERE created_at BETWEEN ? AND ?
    ''', (day, next_day))

    return (first_round, last_round), round_count