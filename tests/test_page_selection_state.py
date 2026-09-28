import unittest

from PyQt5 import QtWidgets

from presenters.page_list_presenter import PageListPresenter


class FakePageListView:
    def __init__(self) -> None:
        self.last_pages = None
        self.summary = None

    def populate_page_table(self, pages):
        self.last_pages = pages

    def update_pagination(self, current, total):
        pass

    def update_selection_summary(self, selected, total):
        self.summary = (selected, total)

    def update_page_info(self, page_id, info):
        pass

    def set_page_post_status(self, page_id, status):
        pass

    def get_token_input(self):
        return ""


class TestPageSelectionState(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_presenter(self, pages=None) -> PageListPresenter:
        view = FakePageListView()
        presenter = PageListPresenter(view=view, main_view=view, page_service=None)
        presenter.pages = pages if pages is not None else [
            {"id": "1", "name": "Page 1"},
            {"id": "2", "name": "Page 2"},
        ]
        return presenter

    def test_select_all_marks_all_pages(self) -> None:
        presenter = self._make_presenter()
        presenter.select_all()
        self.assertEqual(presenter.page_selection_states, {"|1": True, "|2": True})

    def test_clear_selection_unmarks_all_pages(self) -> None:
        presenter = self._make_presenter()
        presenter.select_all()
        presenter.clear_selection()
        self.assertEqual(presenter.page_selection_states, {"|1": False, "|2": False})

    def test_get_selected_pages_respects_state(self) -> None:
        presenter = self._make_presenter()
        presenter.page_selection_states[presenter.page_key({"id": "2"})] = True
        selected = presenter.get_selected_pages()
        self.assertEqual([p["id"] for p in selected], ["2"])

    def test_get_selected_pages_use_all(self) -> None:
        presenter = self._make_presenter()
        selected = presenter.get_selected_pages(use_all=True)
        self.assertEqual([p["id"] for p in selected], ["1", "2"])

    def test_duplicate_page_id_different_accounts_are_independent(self) -> None:
        pages = [
            {"id": "999", "name": "Dup", "account_token": "tokenA"},
            {"id": "999", "name": "Dup", "account_token": "tokenB"},
        ]
        presenter = self._make_presenter(pages)
        key_a, key_b = presenter.page_key(pages[0]), presenter.page_key(pages[1])
        self.assertNotEqual(key_a, key_b)

        # chỉ chọn 1 trong 2 dòng trùng -> dòng kia không bị chọn
        presenter.page_selection_states[key_a] = True
        selected = presenter.get_selected_pages()
        self.assertEqual([p["key"] for p in selected], [key_a])

        # xoá chỉ dòng đang chọn -> dòng trùng còn lại vẫn giữ
        presenter.remove_pages([key_a])
        self.assertEqual(len(presenter.pages), 1)
        self.assertEqual(presenter.page_key(presenter.pages[0]), key_b)


if __name__ == "__main__":
    unittest.main()
