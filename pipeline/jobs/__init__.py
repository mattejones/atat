"""
Generation jobs — async generative work with a reviewable draft stage.

Callers use pipeline.jobs.service (create / update_draft / submit / cancel / retry /
get / list_jobs / wait). Job kinds live in pipeline.jobs.kinds; execution in
pipeline.jobs.runner. See db/migrations/010_generation_jobs.sql for the lifecycle.
"""
