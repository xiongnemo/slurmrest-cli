"""Unit tests for the rendering helpers. No network, no cluster."""

from slurmrest import format as fmt


def test_flat_handles_lists():
    assert fmt._flat(["IDLE", "DRAIN"]) == "IDLE,DRAIN"


def test_flat_handles_slurm_number_objects():
    assert fmt._flat({"set": True, "infinite": False, "number": 42}) == "42"
    assert fmt._flat({"set": True, "infinite": True, "number": 0}) == "infinite"
    assert fmt._flat({"set": False, "infinite": False, "number": 0}) == ""


def test_flat_handles_current_state():
    assert fmt._flat({"current": ["COMPLETED"]}) == "COMPLETED"


def test_tres_str_get():
    s = "cpu=64,mem=500000M,node=1,billing=64,gres/gpu=4"
    assert fmt.tres_str_get(s, "gres/gpu") == "4"
    assert fmt.tres_str_get(s, "cpu") == "64"
    assert fmt.tres_str_get(s, "nope") == ""
    assert fmt.tres_str_get(None, "cpu") == ""


def test_tres_list_filtering():
    entries = [
        {"type": "cpu", "name": "", "count": 64},
        {"type": "gres", "name": "gpu", "count": 4},
    ]
    assert fmt._tres(entries, want="gres") == "gres/gpu=4"
    assert "cpu=64" in fmt._tres(entries)


def test_secs_formats_durations():
    assert fmt._secs("3661") == "1:01:01"
    assert fmt._secs("0") == "0:00:00"
    assert fmt._secs("") == ""


def test_elapsed_from_start_and_end():
    job = {
        "start_time": {"set": True, "number": 1000},
        "end_time": {"set": True, "number": 1075},
    }
    assert fmt._elapsed(job) == "0:01:15"


def test_elapsed_is_blank_when_not_started():
    assert fmt._elapsed({"start_time": {"set": False, "number": 0}}) == ""


def test_jobs_table_flags_allocation_larger_than_request():
    """Exclusive partitions grant the whole node; the table should say so."""
    jobs = [
        {
            "job_id": 1,
            "name": "j",
            "job_state": ["RUNNING"],
            "tres_alloc_str": "cpu=64,gres/gpu=4",
            "tres_req_str": "cpu=1,gres/gpu=2",
        }
    ]
    rendered = fmt.jobs_table(jobs)
    cells = [c for col in rendered.columns for c in col._cells]
    assert any("req 2" in str(c) for c in cells)


def test_jobs_table_quiet_when_request_matches():
    jobs = [
        {
            "job_id": 2,
            "name": "j",
            "job_state": ["RUNNING"],
            "tres_alloc_str": "gres/gpu=4",
            "tres_req_str": "gres/gpu=4",
        }
    ]
    cells = [c for col in fmt.jobs_table(jobs).columns for c in col._cells]
    assert not any("req" in str(c) for c in cells)


def test_cpu_load_is_scaled_back_from_slurms_integer():
    """Slurm sends load average * 100; 5 means 0.05, not 5."""
    assert fmt._cpu_load(5) == "0.05"
    assert fmt._cpu_load(1) == "0.01"
    assert fmt._cpu_load(250) == "2.50"
    assert fmt._cpu_load(None) == ""
