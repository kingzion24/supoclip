"""Katakata Studio: turn a described idea into a narrated stickman video.

Adapted from the Stickman Video Director skill
(https://github.com/kaomei/stickman-video-director, MIT). A director agent
researches the topic, writes a Kiswahili script in a 5-stage arc split into
~10-second scenes, and after approval writes one standalone video prompt per
scene. Clips are generated outside Katakata (Google Flow) and uploaded back;
Katakata then voices the script with Edge TTS and assembles the final video
with word-synced captions and music.
"""
