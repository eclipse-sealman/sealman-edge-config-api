from routers.module_config.secrets import mask_config, mask_text


def test_mask_config():
    sha = "a1b2c3d4" + "0" * 56
    mask = mask_text("client.key", sha)
    assert mask == "<secret: client.key #a1b2c3d4>"
    config = {"a": {"key": "KEY"}, "list": ["x", {"k": "KEY2"}], "other": "keep"}
    paths = [{"path": "/a/key", "name": "client.key", "sha256": sha},
             {"path": "/list/1/k", "name": "client.key", "sha256": sha},
             {"path": "/gone/x", "name": "client.key", "sha256": sha},
             {"path": "/list/7", "name": "client.key", "sha256": sha}]
    masked = mask_config(config, paths)
    assert masked == {"a": {"key": mask}, "list": ["x", {"k": mask}], "other": "keep"}
    assert config["a"]["key"] == "KEY" and config["list"][1]["k"] == "KEY2"
