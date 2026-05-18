import uuid
import yaml
from typing import List
from routers.compose_deployments.schemas import ServiceConfig


class LPComposeBuilder:
    """
    Landing-Page compose builder
    """

    @classmethod
    def build_labels(cls, s: ServiceConfig) -> List[str]:
        labels = [
            "traefik.enable=true",
            f"traefik.http.routers.{s.name}.rule=PathPrefix(`/{s.name}`)",
            f"traefik.http.routers.{s.name}.entrypoints=websecure",
            f"traefik.http.routers.{s.name}.tls=true",
            f"traefik.http.middlewares.{s.name}-strip.stripprefix.prefixes=/{s.name}",
            f"traefik.http.routers.{s.name}.middlewares={s.name}-strip",
            f"traefik.http.services.{s.name}.loadbalancer.server.port={s.serving_http_port}",
        ]

        if s.tile_enabled:
            labels += [
                "tile=true",
                f"tileTitle={s.tile_title}",
                f"tileDescription={s.tile_description}",
                f"tileGroup={s.tile_group}",
                f"tileIcon={s.tile_icon}",
                f"baseURL=/{s.name}",
            ]

        # add label that indicates that this stack was deployed with sealmans local compose mechanic
        labels += ["sealmanLocalCompose=true"]

        return labels

    @classmethod
    def build_service(cls, s: ServiceConfig) -> dict:
        service = {
            "image": s.image,
            "container_name": f"{s.name}-{uuid.uuid4().hex}",
            "labels": cls.build_labels(s),
            "networks": ["landing"]
        }
        if s.exposed_ports:
            service["ports"] = []
            for ex_port in s.exposed_ports:
                service["ports"].append(ex_port)
        if s.env:
            service["environment"] = s.env
        if s.volumes:
            service["volumes"] = s.volumes
        return service

    @classmethod
    def build_compose(cls, name, services: List[ServiceConfig]) -> dict:
        compose = {
            "version": "3.9",
            "name": name,
            "services": {},
            "networks": {
                "landing": {"external": True, "name": "landing"}
            }
        }
        for service in services:
            compose["services"][service.name] = cls.build_service(service)
        return compose

    @classmethod
    def gen_sems_compose(cls, stack):
        name = stack.get("name")
        compose_string = yaml.dump(stack.get("compose"))
        return {name: compose_string}
