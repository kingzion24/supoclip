-- AI-written post caption and hashtags for publishing each clip.
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS post_caption TEXT;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hashtags TEXT;
