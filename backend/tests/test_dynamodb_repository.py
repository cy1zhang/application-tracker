from app.repositories.dynamodb import DynamoApplicationRepository


def test_repository_starts_with_no_applications(
    repository: DynamoApplicationRepository,
) -> None:
    page = repository.list(owner_id="user-a", limit=20, cursor=None)

    assert page.items == []
    assert page.next_cursor is None
