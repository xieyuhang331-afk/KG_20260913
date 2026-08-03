class MemberDomainError(ValueError):
    """Base error for invalid Member Domain values."""


class InvalidMemberIdError(MemberDomainError):
    pass


class InvalidMemberNoError(MemberDomainError):
    pass


class InvalidMemberStatusError(MemberDomainError):
    pass


class InvalidCreationSourceError(MemberDomainError):
    pass
