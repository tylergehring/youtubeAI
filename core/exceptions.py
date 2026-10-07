"""Exception hierarchy. Every deliberate application error derives from VidgenError."""


class VidgenError(Exception):
    """Base class for all application errors."""


class ConfigError(VidgenError):
    """Invalid or missing configuration."""


class InvalidInputError(VidgenError):
    """User-supplied input (URL, job ID, parameters) failed validation."""


class JobError(VidgenError):
    """Base class for job-management errors."""


class JobNotFoundError(JobError):
    """The requested job does not exist."""


class JobExistsError(JobError):
    """A job with that ID already exists."""


class InvalidTransitionError(JobError):
    """A job status change that the state machine does not allow."""


class UpstreamNotReadyError(JobError):
    """A stage was started before the stages it depends on succeeded."""


class StageError(VidgenError):
    """A pipeline stage failed."""


class ProviderError(VidgenError):
    """An external or mock provider failed."""


class StructuredOutputError(ProviderError):
    """An LLM returned output that does not match the requested schema."""


class RenderError(VidgenError):
    """Rendering or media tooling (FFmpeg) failed."""


class QualityCheckError(StageError):
    """Automated technical quality control found failing checks."""
